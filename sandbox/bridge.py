"""PTY bridge for the interactive terminal sandbox.

Runs INSIDE the terminal container, launched by the backend via
``docker exec -i <container> python3 /ptymaster.py`` (this file is baked into
the image, so its source carries no shell quoting across the CLI).

It allocates a pseudo-terminal with ``pty.openpty()``, forks a child that
becomes a new session and executes ``bash --login`` on the slave side, and the
parent relays bytes between the exec stdin (fd 0), the pty master, and the
exec stdout (fd 1).

Framing — exec stdin (fd 0) -> bridge. First byte is the operation:

    0x01 <uint32 BE length> <raw bytes>     INPUT  : write bytes into the pty
    0x02 <uint16 BE rows>  <uint16 BE cols>  RESIZE : resize the pty

bridge -> exec stdout (fd 1) is RAW pty output (bash's stdout+stderr); the
client feeds it straight into xterm.js.

Stdlib only. Python >= 3.8.
"""

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import termios

OP_INPUT = 0x01
OP_RESIZE = 0x02
CHUNK = 65536
INITIAL_ROWS = 24
INITIAL_COLS = 80


def set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def main() -> None:
    master, slave = pty.openpty()
    set_winsize(master, INITIAL_ROWS, INITIAL_COLS)

    child_pid = os.fork()
    if child_pid == 0:
        # Child: new session, pty as controlling tty, then exec bash.
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        try:
            os.execvpe(
                "/bin/bash",
                ["/bin/bash", "--login"],
                dict(os.environ, TERM="xterm-256color"),
            )
        except Exception:  # exec failed — surface a code rather than hang
            os.write(2, b"\n[bridge: failed to exec bash]\n")
            os._exit(127)
        os._exit(127)  # fallback (unreachable, execvpe does not return)

    # Parent: relay between the pty master and the exec streams.
    os.close(slave)

    stdin_fd = 0
    stdout_fd = 1
    buf = b""
    child_reaped = False

    def reap_once() -> None:
        nonlocal child_reaped
        if child_reaped:
            return
        while True:
            try:
                done, _status = os.waitpid(child_pid, os.WNOHANG)
            except ChildProcessError:
                child_reaped = True
                return
            if done == 0:
                return
            child_reaped = True

    def read_master() -> bytes:
        try:
            return os.read(master, CHUNK)
        except OSError as exc:
            # PTY EOF surfaces as EIO (or EAGAIN) once bash is gone.
            if exc.errno in (errno.EIO, errno.EAGAIN, errno.EBADF, 5):  # 5 == EIO on some libcs
                return b""
            raise

    def send(data: bytes) -> None:
        if not data:
            return
        try:
            os.write(stdout_fd, data)
        except (BrokenPipeError, OSError):
            pass

    while True:
        reap_once()
        if child_reaped:
            # Child already exited: drain any final pty bytes, then terminate.
            tail = b""
            while True:
                more = read_master()
                if not more:
                    break
                tail += more
            send(tail)
            send(b"\r\n[terminal exited]\r\n")
            break

        try:
            readable, _w, _e = select.select([stdin_fd, master], [], [])
        except (OSError, ValueError):
            break

        if stdin_fd in readable:
            try:
                chunk = os.read(stdin_fd, CHUNK)
            except OSError:
                chunk = b""
            if chunk:
                buf += chunk
            while buf:  # drain EVERY complete frame present in the buffer
                op = buf[0]
                if op == OP_INPUT:
                    if len(buf) < 5:
                        break
                    n = int.from_bytes(buf[1:5], "big")
                    if len(buf) < 5 + n:
                        break  # partial payload; wait for the next read
                    try:
                        os.write(master, buf[5:5 + n])
                    except OSError:
                        pass
                    buf = buf[5 + n:]
                elif op == OP_RESIZE:
                    if len(buf) < 5:
                        break
                    rows, cols = struct.unpack(">HH", buf[1:5])
                    buf = buf[5:]
                    if rows and cols:
                        set_winsize(master, rows, cols)
                else:
                    buf = buf[1:]  # unknown control byte — drop, resynch

        if master in readable:
            out = read_master()
            if not out:
                # PTY EOF: bash has exited — drain the rest and signal the end.
                reap_once()
                tail = b""
                while True:
                    more = read_master()
                    if not more:
                        break
                    tail += more
                send(tail)
                send(b"\r\n[terminal exited]\r\n")
                break
            send(out)

    # Best-effort cleanup of any surviving bash child (e.g. we left the loop on a
    # fault or stdin EOF before the pty reported EOF).
    try:
        os.kill(child_pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


if __name__ == "__main__":
    main()

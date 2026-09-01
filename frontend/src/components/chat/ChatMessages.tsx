import { useChat } from "../../store/chat";
import { AssistantBody } from "./AssistantBody";

export function ChatMessages() {
  const { messages } = useChat();

  return (
    <>
      {messages.map((m) =>
        m.role === "user" ? (
          <div key={m.id} className="msg msg-user">
            {m.content}
          </div>
        ) : (
          <div key={m.id} className="msg msg-assistant">
            <AssistantBody msg={m} />
          </div>
        ),
      )}
    </>
  );
}

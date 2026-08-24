import { useEffect } from "react";
import Login from "./components/Login";
import Shell from "./components/Shell";
import { useAuth } from "./store/auth";

export default function App() {
  const status = useAuth((s) => s.status);

  useEffect(() => {
    void useAuth.getState().init();
  }, []);

  if (status === "loading") {
    return (
      <div className="center-screen">
        <div className="spinner" aria-label="loading" />
      </div>
    );
  }

  return status === "loggedIn" ? <Shell /> : <Login />;
}

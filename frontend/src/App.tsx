import { useEffect } from "react";
import Shell from "./components/Shell";
import { useAuth } from "./store/auth";

export default function App() {
  const status = useAuth((s) => s.status);

  useEffect(() => {
    void useAuth.getState().init();
  }, []);

  useEffect(() => {
    // No login page: identity.folding-os.com owns the login form. A brief
    // "loading" spinner covers this window before the browser navigates
    // away (matching every other SSO app in the estate).
    if (status === "loggedOut") {
      window.location.href = "/api/auth/login";
    }
  }, [status]);

  if (status !== "loggedIn") {
    return (
      <div className="center-screen">
        <div className="spinner" aria-label="loading" />
      </div>
    );
  }

  return <Shell />;
}

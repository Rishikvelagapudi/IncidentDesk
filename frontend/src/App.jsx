import { useEffect, useState } from "react";
import Dashboard from "./Dashboard.jsx";
import Workbench from "./Workbench.jsx";
import "./styles.css";

export default function App() {
  const [user] = useState("Operator");
  const [path, setPath] = useState(location.pathname);

  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = (p) => {
    history.pushState({}, "", p);
    setPath(p);
    window.scrollTo(0, 0);
  };

  const m = path.match(/^\/incidents\/([\w-]+)/);
  return m ? (
    <Workbench id={m[1]} user={user} navigate={navigate} />
  ) : (
    <Dashboard user={user} navigate={navigate} />
  );
}

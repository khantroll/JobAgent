import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

const base = import.meta.env.BASE_URL;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename={base === "/" ? undefined : base.replace(/\/$/, "")}>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);

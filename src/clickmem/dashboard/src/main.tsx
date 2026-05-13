import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("ClickMem dashboard: #root element not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <BrowserRouter basename="/dashboard">
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import "./appearance.css";
import "./code.css";
import "./skills.css";
import "./models.css";
import "./motion.css";
import "./sunshine.css";
import "./themes.css";
import { applyTo, readAppearance } from "./appearance";
import { startThemeSync } from "./themeSync";

applyTo(document.documentElement, readAppearance());
startThemeSync(readAppearance());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

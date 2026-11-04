import { Link } from "react-router-dom";
import { PRODUCT, PRODUCT_LINE } from "../product";

/** Where the work lives, and the only times it leaves this computer. */
export default function Privacy() {
  return (
    <div className="page" style={{ maxWidth: 640 }}>
      <div className="page-head">
        <div>
          <h1>Privacy</h1>
          <p className="muted">{PRODUCT}. {PRODUCT_LINE}</p>
        </div>
      </div>
      <div className="card col" style={{ gap: 18 }}>
        <div>
          <h2>The lock</h2>
          <p className="muted" style={{ margin: "6px 0 0" }}>A PIN or password you create keeps this copy to you. It is separate from Gmail, Apple, Microsoft, ChatGPT and Claude.</p>
        </div>
        <div>
          <h2>What stays here</h2>
          <p className="muted" style={{ margin: "6px 0 0" }}>Mail you have already opened, calendar, tasks, notes, files, and what the assistant remembers are stored on this computer. Keys sit in this computer's keychain.</p>
        </div>
        <div>
          <h2>What leaves</h2>
          <p className="muted" style={{ margin: "6px 0 0" }}>When you ask the assistant, that question goes to the model you picked. When you sign in to mail or a plan, that company's own window handles the sign-in. Rooks does not sell your work, and it does not send it anywhere except the model or account you chose.</p>
        </div>
        <div>
          <h2>Remembering</h2>
          <p className="muted" style={{ margin: "6px 0 0" }}>Learning about you can be switched off on <Link to="/about-you">About you</Link>. What it has learned stays on this computer.</p>
        </div>
        <div>
          <h2>A phone</h2>
          <p className="muted" style={{ margin: "6px 0 0" }}>A phone you pair can reach this computer. The work still lives here. Unpair it from Settings whenever you want.</p>
        </div>
      </div>
      <p className="small muted"><Link to="/about">About</Link></p>
    </div>
  );
}

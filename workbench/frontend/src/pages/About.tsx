import { Link } from "react-router-dom";
import { PRODUCT, PRODUCT_LINE } from "../product";

/** What Rooks is. Plain words, no setup steps. */
export default function About() {
  return (
    <div className="page" style={{ maxWidth: 640 }}>
      <div className="page-head">
        <div>
          <h1>About</h1>
          <p className="muted">{PRODUCT}. {PRODUCT_LINE}</p>
        </div>
      </div>
      <div className="card col" style={{ gap: 14 }}>
        <p style={{ margin: 0 }}>Rooks runs on this computer. Chat, mail, calendar, tasks, notes, files and the rest of the work stay with you.</p>
        <p style={{ margin: 0 }}>Sign in with the accounts you already use when you want them. Gmail, Apple, Microsoft, ChatGPT and Claude each open that company's own window. You can skip that and add them later.</p>
        <p style={{ margin: 0 }}>The assistant uses the model you choose. That can be a key you paste, a plan you already pay for, or a model on this computer.</p>
        <p style={{ margin: 0 }}>A phone you pair opens the same work. The work still lives here.</p>
      </div>
      <p className="small muted"><Link to="/privacy">Privacy</Link></p>
    </div>
  );
}

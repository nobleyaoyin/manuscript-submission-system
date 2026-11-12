import "./RoleSelection.css";
import { roleLabels } from "./roleLabels";
import { PRODUCT_DISPLAY_NAME } from "./branding";

const roles = [
  { id: "eic", icon: "☆", description: "Oversee submissions, coordinate editors and reviewers, and make editorial decisions." },
  { id: "editor", icon: "✎", description: "Work on assigned manuscripts and communicate with the editorial team." },
  { id: "reviewer", icon: "⌕", description: "Review assigned manuscripts and submit your evaluations." },
  { id: "author", icon: "▤", description: "Submit manuscripts, track progress, and respond to editorial requests." },
];

export default function RoleSelection({ onSelect, error }) {
  return (
    <main className="role-page">
      <div className="role-shell">
        <header className="role-heading">
          <h1>{PRODUCT_DISPLAY_NAME}<span>.</span></h1>
          <p>Journal management system <span aria-hidden="true">—</span> select your role</p>
        </header>
        {error && <p className="role-error" role="alert">{error}</p>}
        <div className="role-grid" aria-label="Choose a sign-in role">
          {roles.map(role => (
            <button key={role.id} type="button" className={`role-card role-${role.id}`} onClick={() => onSelect(role.id)} aria-labelledby={`role-${role.id}-name`} aria-describedby={`role-${role.id}-description`}>
              <span className="role-icon" aria-hidden="true">{role.icon}</span>
              <span id={`role-${role.id}-name`} className="role-name">{roleLabels[role.id]}</span>
              <span id={`role-${role.id}-description`} className="role-description">{role.description}</span>
            </button>
          ))}
        </div>
        <div className="role-secondary">
          <p>Working on formatting?</p>
          <button type="button" onClick={() => onSelect("formatter")}>Formatter sign in <span aria-hidden="true">→</span></button>
        </div>
        <p className="role-note">Your account permissions are determined by the system after sign-in.</p>
      </div>
    </main>
  );
}

/**
 * Login page — sets the actor identity for dev_headers mode.
 *
 * In production (oidc mode), this would redirect to the identity provider.
 * For now it's a simple form that exercises the role model, matching how
 * every curl check in PROGRESS.md sets ``X-Actor`` and ``X-Actor-Role``.
 */

import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth";

const ROLES = [
  "analyst",
  "bench_tech",
  "prep_tech",
  "lab_manager",
  "supervisor",
  "admin",
  "client",
];

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [subject, setSubject] = useState("");
  const [role, setRole] = useState("analyst");

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!subject.trim()) return;
    login({
      subject: subject.trim(),
      name: subject.trim(),
      role,
    });
    navigate("/");
  };

  return (
    <div className="login-page">
      <h1>Sign in to MSA LIMS</h1>
      <form onSubmit={handleSubmit}>
        <label>
          Identity
          <input
            type="text"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="e.g. analyst@lab"
            required
          />
        </label>
        <label>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Sign in</button>
      </form>
    </div>
  );
}

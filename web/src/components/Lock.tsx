import { useState } from "react";
import { api } from "../api";
import { Mark, Shield } from "../icons";

export function Lock({ checking, onUnlock }: { checking: boolean; onUnlock: () => void }) {
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");

  return (
    <main className="lock">
      <div className="lock-card">
        <Mark size={44} />
        <h1>Docufy</h1>
        {checking ? (
          <p className="hint">Opening</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              api.login(password).then(onUnlock).catch((exc: Error) => setNotice(exc.message));
            }}
          >
            <p className="hint">
              <Shield size={15} /> Enter the app password for this computer.
            </p>
            <label className="field">
              <span className="field-label">App password</span>
              <input type="password" autoFocus value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            {notice ? <p className="error-text">{notice}</p> : null}
            <button className="primary-button wide" type="submit" disabled={!password}>
              Unlock
            </button>
          </form>
        )}
      </div>
    </main>
  );
}

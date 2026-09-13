import { Link, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth";
import { setAuthHeaders } from "./api";
import { BatchDetail } from "./pages/BatchDetail";
import { BatchList } from "./pages/BatchList";
import { LoginPage } from "./pages/LoginPage";
import { MultiElementImport } from "./pages/MultiElementImport";
import { SampleDetail } from "./pages/SampleDetail";
import { SampleList } from "./pages/SampleList";
import { SampleProvenance } from "./pages/SampleProvenance";
import { SystemStatus } from "./pages/SystemStatus";

function AppInner() {
  const { user, logout, headers } = useAuth();

  // Keep api.ts in sync with auth state
  setAuthHeaders(headers);

  return (
    <>
      <nav className="topnav">
        <Link to="/" className="brand">
          MSA LIMS
        </Link>
        <Link to="/samples">Samples</Link>
        <Link to="/batches">Batches</Link>
        <Link to="/status">Status</Link>
        <span className="nav-spacer" />
        {user ? (
          <>
            <span className="nav-user">
              {user.name} ({user.role})
            </span>
            <button className="nav-btn" onClick={logout}>
              Sign out
            </button>
          </>
        ) : (
          <Link to="/login">Sign in</Link>
        )}
      </nav>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={user ? <SampleList /> : <LoginPage />}
        />
        <Route
          path="/samples"
          element={user ? <SampleList /> : <LoginPage />}
        />
        <Route
          path="/samples/:id"
          element={user ? <SampleDetail /> : <LoginPage />}
        />
        <Route
          path="/samples/:id/multi-element"
          element={user ? <MultiElementImport /> : <LoginPage />}
        />
        <Route
          path="/samples/:id/provenance"
          element={user ? <SampleProvenance /> : <LoginPage />}
        />
        <Route
          path="/batches"
          element={user ? <BatchList /> : <LoginPage />}
        />
        <Route
          path="/batches/:id"
          element={user ? <BatchDetail /> : <LoginPage />}
        />
        <Route
          path="/status"
          element={user ? <SystemStatus /> : <LoginPage />}
        />
      </Routes>
    </>
  );
}

export function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}

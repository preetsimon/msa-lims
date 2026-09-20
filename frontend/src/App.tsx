import { Link, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth";
import { setAuthHeaders } from "./api";
import { BatchDetail } from "./pages/BatchDetail";
import { BatchList } from "./pages/BatchList";
import { CertificatesPage } from "./pages/CertificatesPage";
import { ClientsPage } from "./pages/ClientsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { InstrumentsPage } from "./pages/InstrumentsPage";
import { LoginPage } from "./pages/LoginPage";
import { MultiElementImport } from "./pages/MultiElementImport";
import { PrepRecordsPage } from "./pages/PrepRecordsPage";
import { ResultEntryPage } from "./pages/ResultEntryPage";
import { SampleDetail } from "./pages/SampleDetail";
import { SampleList } from "./pages/SampleList";
import { SampleProvenance } from "./pages/SampleProvenance";
import { SubmissionsPage } from "./pages/SubmissionsPage";
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
        <Link to="/dashboard">Dashboard</Link>
        <Link to="/samples">Samples</Link>
        <Link to="/submissions">Submissions</Link>
        <Link to="/batches">Batches</Link>
        <Link to="/certificates">Certificates</Link>
        <Link to="/instruments">Instruments</Link>
        <Link to="/clients">Clients</Link>
        <Link to="/result-entry">Enter Result</Link>
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
          element={user ? <DashboardPage /> : <LoginPage />}
        />
        <Route
          path="/dashboard"
          element={user ? <DashboardPage /> : <LoginPage />}
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
          path="/submissions"
          element={user ? <SubmissionsPage /> : <LoginPage />}
        />
        <Route
          path="/certificates"
          element={user ? <CertificatesPage /> : <LoginPage />}
        />
        <Route
          path="/instruments"
          element={user ? <InstrumentsPage /> : <LoginPage />}
        />
        <Route
          path="/clients"
          element={user ? <ClientsPage /> : <LoginPage />}
        />
        <Route
          path="/samples/:id/prep-records"
          element={user ? <PrepRecordsPage /> : <LoginPage />}
        />
        <Route
          path="/result-entry"
          element={user ? <ResultEntryPage /> : <LoginPage />}
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

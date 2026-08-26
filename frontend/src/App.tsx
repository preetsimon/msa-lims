import { Link, Route, Routes } from "react-router-dom";

import { BatchDetail } from "./pages/BatchDetail";
import { BatchList } from "./pages/BatchList";
import { SampleDetail } from "./pages/SampleDetail";
import { SampleList } from "./pages/SampleList";
import { SystemStatus } from "./pages/SystemStatus";

export function App() {
  return (
    <>
      <nav className="topnav">
        <Link to="/" className="brand">
          MSA LIMS
        </Link>
        <Link to="/samples">Samples</Link>
        <Link to="/batches">Batches</Link>
        <Link to="/status">Status</Link>
      </nav>
      <Routes>
        <Route path="/" element={<SampleList />} />
        <Route path="/samples" element={<SampleList />} />
        <Route path="/samples/:id" element={<SampleDetail />} />
        <Route path="/batches" element={<BatchList />} />
        <Route path="/batches/:id" element={<BatchDetail />} />
        <Route path="/status" element={<SystemStatus />} />
      </Routes>
    </>
  );
}

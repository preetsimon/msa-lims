/**
 * Authentication context and provider.
 *
 * Two modes, matching the backend's ``MSA_AUTH_MODE``:
 *
 * - **dev_headers**: a simple form sets actor/role headers — no real auth,
 *   just enough to exercise the role model in development.
 * - **oidc**: redirects to the identity provider, handles the callback,
 *   and stores the bearer token in memory.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface AuthUser {
  subject: string;
  name: string;
  role: string;
  token?: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  login: (user: AuthUser) => void;
  logout: () => void;
  headers: Record<string, string>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    // Restore from sessionStorage on mount
    const stored = sessionStorage.getItem("msa_auth");
    if (stored) {
      try {
        return JSON.parse(stored) as AuthUser;
      } catch {
        sessionStorage.removeItem("msa_auth");
      }
    }
    return null;
  });

  const login = useCallback((u: AuthUser) => {
    setUser(u);
    sessionStorage.setItem("msa_auth", JSON.stringify(u));
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    sessionStorage.removeItem("msa_auth");
  }, []);

  const headers = useMemo(() => {
    if (!user) return {};
    if (user.token) {
      return { Authorization: `Bearer ${user.token}` };
    }
    // Dev headers mode
    return {
      "X-Actor": user.subject,
      "X-Actor-Role": user.role,
    };
  }, [user]);

  const value = useMemo(
    () => ({ user, login, logout, headers }),
    [user, login, logout, headers],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}

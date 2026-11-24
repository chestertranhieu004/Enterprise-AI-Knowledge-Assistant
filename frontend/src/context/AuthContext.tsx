import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { apiClient, TOKEN_STORAGE_KEY, USER_STORAGE_KEY } from '../api/client';
import { DEMO_PASSWORD, LoginRequest, UserRead } from '../types/api';

interface AuthContextValue {
  user: UserRead | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isManagerOrAdmin: boolean;
  canUploadDocuments: boolean;
  canViewUsers: boolean;
  login: (payload: LoginRequest) => Promise<UserRead>;
  quickSwitchAccount: (emailOrUsername: string, password?: string) => Promise<UserRead>;
  logout: () => void;
  refreshCurrentUser: () => Promise<UserRead | null>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function getStoredUser(): UserRead | null {
  try {
    const raw = localStorage.getItem(USER_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as UserRead;
  } catch {
    return null;
  }
}

function setStoredUser(user: UserRead | null): void {
  try {
    if (user) {
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(USER_STORAGE_KEY);
    }
  } catch {
    // Ignore storage errors
  }
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [token, setToken] = useState<string | null>(() =>
    apiClient.getStoredToken()
  );
  const [user, setUser] = useState<UserRead | null>(() => getStoredUser());
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const logout = useCallback(() => {
    apiClient.setStoredToken(null);
    setStoredUser(null);
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    apiClient.setUnauthorizedHandler(() => {
      logout();
    });
    return () => {
      apiClient.setUnauthorizedHandler(null);
    };
  }, [logout]);

  const refreshCurrentUser = useCallback(async (): Promise<UserRead | null> => {
    const activeToken = apiClient.getStoredToken();
    if (!activeToken) {
      setToken(null);
      setUser(null);
      setStoredUser(null);
      return null;
    }

    try {
      const profile = await apiClient.getCurrentUser();
      setToken(activeToken);
      setUser(profile);
      setStoredUser(profile);
      return profile;
    } catch {
      logout();
      return null;
    }
  }, [logout]);

  useEffect(() => {
    let mounted = true;
    const initSession = async () => {
      const storedToken = apiClient.getStoredToken();
      if (!storedToken) {
        if (mounted) {
          setIsLoading(false);
        }
        return;
      }
      await refreshCurrentUser();
      if (mounted) {
        setIsLoading(false);
      }
    };
    void initSession();
    return () => {
      mounted = false;
    };
  }, [refreshCurrentUser]);

  const login = useCallback(async (payload: LoginRequest): Promise<UserRead> => {
    const response = await apiClient.login(payload);
    setToken(response.access_token);
    setUser(response.user);
    setStoredUser(response.user);
    localStorage.setItem(TOKEN_STORAGE_KEY, response.access_token);
    return response.user;
  }, []);

  const quickSwitchAccount = useCallback(
    async (
      emailOrUsername: string,
      password: string = DEMO_PASSWORD
    ): Promise<UserRead> => {
      const isEmail = emailOrUsername.includes('@');
      return login({
        email: isEmail ? emailOrUsername : null,
        username: isEmail ? null : emailOrUsername,
        password,
      });
    },
    [login]
  );

  const value = useMemo<AuthContextValue>(() => {
    const role = user?.role;
    const isAdmin = role === 'admin';
    const isManagerOrAdmin = role === 'admin' || role === 'manager';

    return {
      user,
      token,
      isLoading,
      isAuthenticated: Boolean(token && user),
      isAdmin,
      isManagerOrAdmin,
      canUploadDocuments: isManagerOrAdmin,
      canViewUsers: isManagerOrAdmin,
      login,
      quickSwitchAccount,
      logout,
      refreshCurrentUser,
    };
  }, [
    user,
    token,
    isLoading,
    login,
    quickSwitchAccount,
    logout,
    refreshCurrentUser,
  ]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside an AuthProvider');
  }
  return ctx;
}

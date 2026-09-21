import { useState, useEffect, useCallback, useRef } from 'react';
import { FundData } from '../services/userService';
import { portfolioService } from '../services/portfolioService';
import { shouldUpdateByFingerprint } from '../utils/dataChange';
import { refreshOrchestrator } from '../services/refreshOrchestrator';
import { useAppSelector } from '../store';
import { authService } from '../features/auth/services/authService';

export interface UseFundDataOptions {
  autoRefresh?: boolean;
  refreshInterval?: number;
  userId?: string;
  tenantId?: string;
}

export interface UseFundDataReturn {
  data: FundData | null;
  loading: boolean;
  error: string | null;
  lastUpdate: string | null;
  isSimulated: boolean;
  tradingMode: 'real' | 'simulation';
  refresh: () => Promise<void>;
}

export const useFundData = (options: UseFundDataOptions = {}): UseFundDataReturn => {
  const {
    autoRefresh = true,
    refreshInterval = 30000,
    userId,
    tenantId,
  } = options;

  const tradingMode = useAppSelector((state) => state.ui.tradingMode);
  const [data, setData] = useState<FundData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const [isSimulated, setIsSimulated] = useState<boolean>(tradingMode === 'simulation');
  const fingerprintRef = useRef<string | null>(null);
  const dataRef = useRef<FundData | null>(null);

  const storedUser = authService.getStoredUser() as { id?: string; user_id?: string; tenant_id?: string } | null;
  const resolvedUserId = String(
    userId ||
    storedUser?.user_id ||
    storedUser?.id ||
    ''
  ).trim();
  const resolvedTenantId = String(
    tenantId ||
    storedUser?.tenant_id ||
    localStorage.getItem('tenant_id') ||
    (import.meta as any).env?.VITE_TENANT_ID ||
    'default'
  ).trim() || 'default';

  const fetchData = useCallback(async (params?: { silent?: boolean }) => {
    const silent = params?.silent ?? true;

    try {
      // 静默刷新不打断已有展示，避免大盘「加载慢 / 闪回 100 万」
      if (!silent || !dataRef.current) {
        setLoading(true);
      }
      setError(null);

      const result = await portfolioService.getFundOverview(resolvedUserId, tradingMode, resolvedTenantId);

      const nextSnapshot = {
        data: result.data,
        isSimulated: result.isSimulated,
        mode: tradingMode
      };

      const { changed, fingerprint } = shouldUpdateByFingerprint(fingerprintRef.current, nextSnapshot);

      if (!changed) {
        return;
      }

      dataRef.current = result.data;
      setData(result.data);
      setIsSimulated(result.isSimulated);
      setLastUpdate(result.data.lastUpdate);
      fingerprintRef.current = fingerprint;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '未知错误';
      setError(errorMessage);
      console.error('获取资金数据失败:', errorMessage);

      // 已有成功数据时保留，绝不降级成假 100 万
      if (!dataRef.current) {
        setData(null);
        setLastUpdate(null);
        fingerprintRef.current = null;
      }
    } finally {
      setLoading(false);
    }
  }, [resolvedUserId, resolvedTenantId, tradingMode]);

  const refresh = useCallback(async () => {
    await fetchData({ silent: true });
  }, [fetchData]);

  useEffect(() => {
    fetchData({ silent: false });
  }, [fetchData]);

  useEffect(() => {
    setLoading(true);
    setData(null);
    dataRef.current = null;
    fingerprintRef.current = null;
  }, [tradingMode]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }

    const unregister = refreshOrchestrator.register(
      'fund',
      async () => {
        await fetchData({ silent: true });
      },
      { minIntervalMs: Math.min(Math.max(refreshInterval, 800), 5000) },
    );

    return unregister;
  }, [autoRefresh, refreshInterval, fetchData]);

  return {
    data,
    loading,
    error,
    lastUpdate,
    isSimulated,
    tradingMode,
    refresh,
  };
};

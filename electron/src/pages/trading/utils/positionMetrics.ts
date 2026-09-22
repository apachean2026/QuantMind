import { AccountInfo } from '../../../services/realTradingService';
import { 
    RawPosition, 
    StockNameMap, 
    normalizePositions, 
    resolveCode, 
    resolveName,
    toFiniteNumber, 
    toPositiveNumber 
} from '../../../utils/portfolioUtils';

export interface NormalizedHolding {
    code: string;
    name: string;
    shares: number;
    cost: number;
    current: number;
    profit: number;
    profitPercent: number;
    value: number;
}

export interface PositionSummary {
    totalAsset: number;
    cashValue: number;
    positionValue: number;
    positionRatio: number;
    cashRatio: number;
}

export const extractPositionCodes = (accountInfo: AccountInfo | null): string[] => {
    const rows = normalizePositions(accountInfo);
    return Array.from(new Set(rows.map(({ key, pos }) => resolveCode(key, pos)).filter(Boolean)));
};

export const getPositionSummary = (
    accountInfo: AccountInfo | null,
    holdings?: NormalizedHolding[],
): PositionSummary => {
    const totalAsset = toFiniteNumber(accountInfo?.total_asset, 0);
    const cashValue = toFiniteNumber(
        (accountInfo as any)?.cash ?? (accountInfo as any)?.available_cash,
        0,
    );
    // 传入合并实时价后的持仓时，汇总跟随重算（与明细同口径），否则用账户旧市值
    const positionValue = holdings
        ? holdings.reduce((sum, h) => sum + toFiniteNumber(h.value, 0), 0)
        : toFiniteNumber(accountInfo?.market_value, 0);
    const safeTotalAsset = totalAsset > 0 ? totalAsset : (cashValue + positionValue);
    const positionRatio = safeTotalAsset > 0 ? (positionValue / safeTotalAsset) * 100 : 0;
    const cashRatio = safeTotalAsset > 0 ? (cashValue / safeTotalAsset) * 100 : 0;

    return {
        totalAsset: safeTotalAsset,
        cashValue,
        positionValue,
        positionRatio,
        cashRatio,
    };
};

export const buildNormalizedHoldings = (
    accountInfo: AccountInfo | null,
    stockNames: StockNameMap = {},
): NormalizedHolding[] => {
    const rows = normalizePositions(accountInfo);
    return rows
        .map(({ key, pos }) => {
            const code = resolveCode(key, pos);
            const shares = toFiniteNumber(pos.volume ?? pos.qty ?? pos.quantity ?? pos.total_volume, 0);
            const marketValue = toFiniteNumber(pos.market_value, 0);
            // 模拟盘 Redis 重估只写 `price`/`market_value`，成交时写入的 `last_price`
            // 会停在买价附近。若现价优先 last_price、盈亏却用 market_value，会出现
            // 「成本>现价仍盈利」。现价必须与市值同源：优先 mark price，再推到 last。
            const markPrice = toPositiveNumber(pos.price, NaN);
            const lastPrice = toPositiveNumber(
                pos.last_price ?? pos.current_price,
                NaN,
            );
            const derivedFromMv = shares > 0 && marketValue > 0 ? marketValue / shares : NaN;
            const current = toPositiveNumber(
                Number.isFinite(markPrice) && markPrice > 0
                    ? markPrice
                    : Number.isFinite(derivedFromMv) && derivedFromMv > 0
                      ? derivedFromMv
                      : lastPrice,
                0,
            );

            const providedCost = toPositiveNumber(
                pos.cost_price ?? pos.avg_cost ?? pos.avg_price ?? pos.cost,
                NaN,
            );
            let cost = Number.isFinite(providedCost) ? providedCost : 0;
            if (cost <= 0 && current > 0) {
                cost = current;
            }

            const side = String(pos.side || pos.position_side || 'long').toLowerCase();
            const isShort = side === 'short' || String(key).includes(':short');
            const value = shares > 0 && current > 0 ? shares * current : marketValue;
            // 一律用「展示现价 vs 成本」重算，不信任可能与现价脱节的后端 pnl / market_value
            const profit =
                cost > 0 && current > 0 && shares > 0
                    ? (isShort ? cost - current : current - cost) * shares
                    : 0;
            const costValue = shares * cost;
            const profitPercent = costValue > 0 ? (profit / costValue) * 100 : 0;

            return {
                code,
                name: resolveName(code, pos, stockNames),
                shares,
                cost,
                current,
                profit,
                profitPercent,
                value,
            };
        })
        .filter((item) => item.shares > 0 || item.value > 0)
        .sort((a, b) => b.value - a.value);
};

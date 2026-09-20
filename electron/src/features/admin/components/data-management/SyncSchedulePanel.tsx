import React, { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, message, Space, Switch, TimePicker } from 'antd';
import dayjs, { Dayjs } from 'dayjs';
import { ClockCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { adminService } from '../../services/adminService';

export interface MarketSyncSchedule {
    market: string;
    label: string;
    enabled: boolean;
    time: string;
    days: number;
    datasets: string[];
}

interface SyncSchedulePanelProps {
    /** 市场标识: A / US / HK / BC / FUTURES */
    market: string;
    /** 该市场当前勾选的数据集（用于默认填充） */
    selectedDatasets?: string[];
    defaultDays?: number;
}

/** 每市场定时同步配置面板 — 每天 HH:MM 定时同步上游数据（精确到分钟，建议次日 00:00 以后按需错峰）。 */
export const SyncSchedulePanel: React.FC<SyncSchedulePanelProps> = ({
    market,
    selectedDatasets = [],
    defaultDays = 5,
}) => {
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [running, setRunning] = useState(false);
    const [enabled, setEnabled] = useState(false);
    const [time, setTime] = useState<Dayjs>(dayjs('00:30', 'HH:mm'));
    const [days, setDays] = useState(defaultDays);
    const [datasets, setDatasets] = useState<string[]>([]);

    useEffect(() => {
        loadSchedule();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [market]);

    const loadSchedule = async () => {
        setLoading(true);
        try {
            const resp = await adminService.getSyncSchedule(market);
            if (resp?.data) {
                const s = resp.data;
                setEnabled(!!s.enabled);
                setTime(dayjs(s.time, 'HH:mm').isValid() ? dayjs(s.time, 'HH:mm') : dayjs('00:30', 'HH:mm'));
                setDays(s.days ?? defaultDays);
                setDatasets(s.datasets?.length ? s.datasets : [...selectedDatasets]);
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : '未知错误';
            message.error(`加载定时配置失败: ${msg}`);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await adminService.saveSyncSchedule(market, {
                enabled,
                time: time.format('HH:mm'),
                days,
                datasets,
            });
            message.success('定时同步配置已保存');
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : '未知错误';
            message.error(`保存定时配置失败: ${msg}`);
        } finally {
            setSaving(false);
        }
    };

    const handleRunNow = async () => {
        setRunning(true);
        try {
            await adminService.runSyncScheduleNow(market);
            message.success('已派发同步任务（后台执行）');
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : '未知错误';
            message.error(`触发同步失败: ${msg}`);
        } finally {
            setRunning(false);
        }
    };

    return (
        <div className="rounded-2xl border border-slate-100 bg-slate-50/50 p-4">
            <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                    <ClockCircleOutlined className="text-amber-500" />
                    定时同步 · 每天自动同步上游数据（建议次日 00:00 以后错峰）
                </span>
                <Switch
                    size="small"
                    checked={enabled}
                    onChange={setEnabled}
                    loading={loading}
                    checkedChildren="开"
                    unCheckedChildren="关"
                />
            </div>
            {enabled && (
                <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2 bg-white rounded-xl border border-slate-100 px-3 py-2.5">
                        <span className="text-xs text-slate-500 font-medium">每天</span>
                        <TimePicker
                            size="small"
                            format="HH:mm"
                            minuteStep={5}
                            value={time}
                            onChange={(v) => v && setTime(v)}
                            style={{ width: 96 }}
                        />
                        <span className="text-xs text-slate-500">同步最近</span>
                        <InputNumber
                            size="small"
                            min={1}
                            max={365}
                            value={days}
                            onChange={(v) => setDays(v ?? defaultDays)}
                            style={{ width: 72 }}
                        />
                        <span className="text-xs text-slate-500">
                            {market === 'BC' ? '个自然日' : '个交易日'}
                        </span>
                    </div>
                    <div className="text-[11px] text-slate-400 px-1">
                        {datasets.length > 0
                            ? `将同步：${datasets.join(', ')}（跟随下方勾选）`
                            : '未指定时按该市场默认全量同步'}
                    </div>
                    <div className="text-[11px] text-slate-400 bg-white rounded-lg border border-slate-100 px-3 py-2">
                        后台 Celery 到点自动触发，时区 Asia/Shanghai，请按需错峰避免集中请求。
                    </div>
                </div>
            )}
            <div className="flex gap-2 mt-3">
                <Button size="small" type="primary" className="rounded-lg font-bold" onClick={handleSave} loading={saving}>
                    保存定时配置
                </Button>
                <Button
                    size="small"
                    className="rounded-lg"
                    icon={<ThunderboltOutlined />}
                    onClick={handleRunNow}
                    loading={running}
                    disabled={!enabled}
                >
                    立即同步一次
                </Button>
            </div>
        </div>
    );
};

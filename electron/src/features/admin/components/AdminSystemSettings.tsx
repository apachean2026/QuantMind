import React, { useEffect, useState } from 'react';
import { Switch, Tag, Typography, message, Alert } from 'antd';
import {
    RobotOutlined,
    SettingOutlined,
    CheckCircleFilled,
    CloseCircleFilled,
    LoadingOutlined,
    HddOutlined,
    ThunderboltOutlined,
    ApiOutlined,
} from '@ant-design/icons';
import { adminService } from '../services/adminService';
import { SectionLoading } from '../../../components/common/UnifiedLoading';

const { Title, Text } = Typography;

type FinbertDetail = {
    enabled?: boolean;
    device?: number;
    model?: string;
    installed?: boolean;
    framework_ok?: boolean;
    model_ready?: boolean;
    model_failed?: boolean;
    override?: boolean | null;
    toggle_path?: string;
};

function StatusPill({
    ok,
    pending,
    label,
    value,
    icon,
}: {
    ok?: boolean;
    pending?: boolean;
    label: string;
    value: string;
    icon: React.ReactNode;
}) {
    const tone = pending
        ? 'border-amber-100 bg-amber-50/80 text-amber-800'
        : ok
          ? 'border-emerald-100 bg-emerald-50/70 text-emerald-800'
          : 'border-slate-100 bg-slate-50 text-slate-600';

    return (
        <div className={`rounded-xl border px-3 py-2.5 text-center ${tone}`}>
            <div className="flex items-center justify-center gap-1.5 text-[11px] opacity-70">
                {icon}
                <span>{label}</span>
            </div>
            <div className="mt-1 text-sm font-semibold tracking-tight truncate">{value}</div>
        </div>
    );
}

export const AdminSystemSettings: React.FC = () => {
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [loading, setLoading] = useState(false);
    const [detail, setDetail] = useState<FinbertDetail | null>(null);
    const [initialLoading, setInitialLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const st: any = await adminService.getFinbertStatus();
                if (!cancelled) {
                    const en = !!(st?.enabled ?? st?.data?.enabled);
                    setEnabled(en);
                    setDetail(st?.data ?? st);
                }
            } catch {
                if (!cancelled) setEnabled(null);
            } finally {
                if (!cancelled) setInitialLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const handleToggle = async (checked: boolean) => {
        setLoading(true);
        const prev = enabled;
        setEnabled(checked);
        try {
            const res: any = await adminService.setFinbertEnabled(checked);
            const st = res?.data ?? res;
            const en = !!(st?.enabled ?? checked);
            setEnabled(en);
            setDetail(st);
            message.success(
                `FinBERT 已${en ? '开启' : '关闭'}${
                    st?.model_ready
                        ? '（模型就绪）'
                        : checked
                          ? '（后台加载中，约 10-20s 后生效）'
                          : ''
                }`,
            );
        } catch (e: any) {
            setEnabled(prev);
            message.error(e?.response?.data?.detail || '切换失败，请检查管理员权限');
        } finally {
            setLoading(false);
        }
    };

    if (initialLoading) {
        return <SectionLoading tip="加载系统设置..." minHeight={240} />;
    }

    const modelName = detail?.model || 'bardsai/finance-sentiment-zh-base';
    const notInstalled = !!detail && detail.installed === false;
    const noFramework = !!detail && detail.framework_ok === false;
    const cannotEnable = notInstalled || noFramework;
    const deviceNum = detail?.device ?? -1;
    const deviceLabel = deviceNum === -1 ? 'CPU' : `GPU ${deviceNum}`;
    const readyPending = !detail?.model_ready && !detail?.model_failed && !!enabled;
    const readyLabel = detail?.model_ready
        ? '已就绪'
        : detail?.model_failed
          ? '加载失败'
          : enabled
            ? '加载中'
            : '未加载';
    const statusTone =
        enabled === null
            ? { tag: 'default' as const, text: '未知' }
            : cannotEnable
              ? { tag: 'warning' as const, text: '不可用' }
              : enabled
                ? { tag: 'success' as const, text: '运行中' }
                : { tag: 'default' as const, text: '已关闭' };

    return (
        <div className="w-full space-y-6">
            <header>
                <Title
                    level={4}
                    className="!m-0 !font-black !text-slate-800 flex items-center gap-2"
                >
                    <SettingOutlined className="text-slate-600" /> 系统设置
                </Title>
                <Text className="text-slate-400 text-xs">基础设施与 AI 能力开关</Text>
            </header>

            <section className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
                {/* 主控区 */}
                <div className="px-5 pt-5 pb-4 sm:px-6">
                    <div className="flex items-start justify-between gap-6">
                        <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-slate-900 text-white">
                                    <RobotOutlined />
                                </span>
                                <h3 className="m-0 text-base font-bold text-slate-800">
                                    FinBERT 中文金融情感
                                </h3>
                                <Tag
                                    color={statusTone.tag}
                                    className="m-0 border-none text-[11px]"
                                >
                                    {statusTone.text}
                                </Tag>
                            </div>
                            <p className="mt-2 mb-0 text-xs leading-relaxed text-slate-500">
                                入库资讯走中文金融情感推理；关闭后仅用字典法，CPU
                                零开销。切换即时生效，无需重启。
                            </p>
                        </div>

                        <div className="shrink-0 flex flex-col items-end gap-1.5 pt-0.5">
                            <Switch
                                checked={!!enabled}
                                loading={loading || enabled === null}
                                onChange={handleToggle}
                                checkedChildren="开"
                                unCheckedChildren="关"
                                disabled={cannotEnable}
                            />
                            <span className="text-[11px] text-slate-400">
                                {notInstalled
                                    ? '需先装权重'
                                    : noFramework
                                      ? '需补装框架'
                                      : enabled
                                        ? '推理已启用'
                                        : '仅字典法'}
                            </span>
                        </div>
                    </div>
                </div>

                {/* 状态条 */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 px-5 pb-4 sm:px-6">
                    <StatusPill
                        icon={<ThunderboltOutlined />}
                        label="推理设备"
                        value={deviceLabel}
                        ok={deviceNum !== -1}
                    />
                    <StatusPill
                        icon={
                            readyPending ? (
                                <LoadingOutlined />
                            ) : detail?.model_ready ? (
                                <CheckCircleFilled />
                            ) : (
                                <CloseCircleFilled />
                            )
                        }
                        label="模型状态"
                        value={readyLabel}
                        ok={!!detail?.model_ready}
                        pending={readyPending}
                    />
                    <StatusPill
                        icon={<HddOutlined />}
                        label="权重文件"
                        value={notInstalled ? '未安装' : '已就位'}
                        ok={!notInstalled}
                    />
                    <StatusPill
                        icon={<ApiOutlined />}
                        label="推理框架"
                        value={noFramework ? '缺 torch' : '可用'}
                        ok={!noFramework}
                    />
                </div>

                {/* 阻塞告警 */}
                {(notInstalled || noFramework) && (
                    <div className="px-5 pb-4 sm:px-6 space-y-2">
                        {notInstalled && (
                            <Alert
                                type="warning"
                                showIcon
                                className="rounded-xl text-xs !py-2 !px-3"
                                message="模型权重未安装"
                                description={
                                    <span>
                                        未检测到 <code className="text-[11px]">{modelName}</code>{' '}
                                        （路径{' '}
                                        <code className="text-[11px]">
                                            /app/models/finbert-zh-base
                                        </code>
                                        ）。请先执行{' '}
                                        <code className="text-[11px]">
                                            backend/scripts/download_finbert.py
                                        </code>{' '}
                                        后再开启。
                                    </span>
                                }
                            />
                        )}
                        {noFramework && !notInstalled && (
                            <Alert
                                type="warning"
                                showIcon
                                className="rounded-xl text-xs !py-2 !px-3"
                                message="缺少 PyTorch 推理框架"
                                description={
                                    <span>
                                        权重已就绪，但镜像未含 torch/transformers。请在服务器执行{' '}
                                        <code className="text-[11px]">
                                            sudo bash deploy/install-model-deps.sh
                                        </code>{' '}
                                        补装后重试。
                                    </span>
                                }
                            />
                        )}
                    </div>
                )}

                {/* 页脚元信息 */}
                <div className="border-t border-slate-100 bg-slate-50/60 px-5 py-3 sm:px-6">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                        <Text className="text-[11px] text-slate-400 truncate">
                            模型 <span className="font-mono text-slate-500">{modelName}</span>
                            <span className="mx-1.5 text-slate-300">·</span>
                            约 391M · 离线推理
                        </Text>
                        <Text className="text-[11px] text-slate-400">
                            首次开启约 10–20s 加载；历史资讯需重建 enrichment
                        </Text>
                    </div>
                </div>
            </section>
        </div>
    );
};

export default AdminSystemSettings;

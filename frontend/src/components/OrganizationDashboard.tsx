import { useEffect, useState } from "react";
import { api } from "../api";

interface OrgDashboardStats {
  total_experiments: number;
  total_campaigns: number;
  total_projects: number;
  active_projects: number;
  by_domain: Record<string, number>;
  top_performers: Array<{
    metric: string; value: number; experiment_id: number;
    project_title: string; formulation_preview: string; measured_at: string;
  }>;
  ingredient_frequency: Array<{
    ingredient_name: string; experiment_count: number;
    avg_weight_pct: number; best_result_metric: string | null;
  }>;
  convergence_rate: number;
  avg_rounds_to_converge: number;
  recent_activity: { experiments_added: number; campaigns_created: number };
}

export default function OrganizationDashboard() {
  const [data, setData] = useState<OrgDashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/org/dashboard")
      .then((res) => setData(res.data))
      .catch((err) => console.error("Dashboard load failed:", err))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-slate-500 text-sm p-4">加载中...</div>;
  if (!data) return <div className="text-red-400 text-sm p-4">加载失败</div>;

  const kpiCards = [
    { label: "实验总数", value: data.total_experiments, color: "text-accent" },
    { label: "Campaign 数", value: data.total_campaigns, color: "text-accent2" },
    { label: "项目总数", value: data.total_projects, color: "text-slate-300" },
    { label: "活跃项目", value: data.active_projects, color: "text-emerald-400" },
    { label: "收敛率", value: `${(data.convergence_rate * 100).toFixed(0)}%`, color: "text-amber-400" },
    { label: "平均收敛轮次", value: data.avg_rounds_to_converge, color: "text-slate-400" },
  ];

  return (
    <div className="space-y-4 p-4">
      <h2 className="text-sm font-semibold text-slate-300">组织级 R&D 仪表盘</h2>
      <div className="grid grid-cols-3 gap-2">
        {kpiCards.map((kpi) => (
          <div key={kpi.label} className="rounded-lg border border-edge bg-ink/60 p-3 text-center">
            <div className={`text-xl font-bold ${kpi.color}`}>{kpi.value}</div>
            <div className="text-[10px] text-slate-500 mt-1">{kpi.label}</div>
          </div>
        ))}
      </div>
      {Object.keys(data.by_domain).length > 0 && (
        <div className="rounded-lg border border-edge bg-ink/60 p-3">
          <h3 className="text-xs font-medium text-slate-400 mb-2">领域分布</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.by_domain).map(([domain, count]) => (
              <span key={domain} className="text-[10px] px-2 py-1 rounded bg-accent/10 text-accent border border-accent/20">
                {domain}: {count}
              </span>
            ))}
          </div>
        </div>
      )}
      {data.top_performers.length > 0 && (
        <div className="rounded-lg border border-edge bg-ink/60 p-3">
          <h3 className="text-xs font-medium text-slate-400 mb-2">最佳记录</h3>
          <div className="space-y-1">
            {data.top_performers.map((tp, i) => (
              <div key={i} className="flex items-center justify-between text-[11px] py-1 border-b border-edge/30 last:border-0">
                <div className="flex items-center gap-2">
                  <span className="text-accent font-medium">{tp.metric}</span>
                  <span className="text-slate-300">{tp.value}</span>
                </div>
                <div className="text-slate-500">{tp.project_title} · {tp.formulation_preview}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {data.ingredient_frequency.length > 0 && (
        <div className="rounded-lg border border-edge bg-ink/60 p-3">
          <h3 className="text-xs font-medium text-slate-400 mb-2">常用原料 Top 20</h3>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {data.ingredient_frequency.map((ing, i) => (
              <div key={i} className="flex items-center justify-between text-[10px]">
                <span className="text-slate-300 truncate max-w-[120px]">{ing.ingredient_name}</span>
                <span className="text-slate-500">{ing.experiment_count}次 · avg {ing.avg_weight_pct}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="rounded-lg border border-edge bg-ink/60 p-3">
        <h3 className="text-xs font-medium text-slate-400 mb-2">最近 7 天活动</h3>
        <div className="flex gap-4 text-[11px]">
          <span className="text-accent">+{data.recent_activity.experiments_added} 实验</span>
          <span className="text-accent2">+{data.recent_activity.campaigns_created} Campaign</span>
        </div>
      </div>
    </div>
  );
}

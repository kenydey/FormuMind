// Reserved 3D simulation viewport. The cure/interface trajectory from the
// HTPolyNet/LAMMPS pipeline (via 3Dmol.js / OVITO) will render here.
export default function SimPlaceholder() {
  return (
    <div className="glass rounded-xl p-4 flex flex-col items-center justify-center text-center relative overflow-hidden">
      <div className="absolute inset-0 opacity-20 bg-[radial-gradient(circle_at_50%_40%,#22d3ee,transparent_60%)]" />
      <div className="relative z-10">
        <div className="text-accent2 text-sm uppercase tracking-widest mb-2">3D 仿真 · Reserved</div>
        <div className="grid grid-cols-6 gap-1 mb-3 mx-auto w-fit">
          {Array.from({ length: 24 }).map((_, i) => (
            <div key={i} className="w-2.5 h-2.5 rounded-full bg-accent/30 animate-pulse" style={{ animationDelay: `${i * 60}ms` }} />
          ))}
        </div>
        <p className="text-xs text-slate-500 max-w-xs">
          交联网络 / 界面吸附轨迹占位。后续由 HTPolyNet · LUNAR · LAMMPS 计算，经 3Dmol.js / OVITO 渲染。
        </p>
      </div>
    </div>
  );
}

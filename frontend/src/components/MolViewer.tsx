/**
 * MolViewer — reserved 3D molecular-structure viewer.
 *
 * Placeholder only: this defines the data contract (a list of named SMILES)
 * and renders a labelled stub. The real implementation will mount a 3Dmol.js
 * WebGL canvas (`npm install 3dmol`) and render ball-and-stick models from the
 * SMILES, without requiring the heavy MD-simulation engines. Until then it
 * stays inert so it adds no runtime weight to the bundle.
 */

export interface MolEntry {
  name: string;
  smiles?: string | null;
}

export default function MolViewer({ entries }: { entries: MolEntry[] }) {
  const withSmiles = entries.filter((e) => e.smiles);
  return (
    <div className="border border-dashed border-edge rounded-lg p-3 bg-ink/40">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">🧬</span>
        <span className="text-xs uppercase tracking-widest text-accent2">
          3D 分子视图 · Molecular Viewer
        </span>
        <span className="ml-auto text-[10px] font-mono rounded-full border border-amber-500/40 bg-amber-500/20 text-amber-400 px-1.5 py-0.5">
          即将上线
        </span>
      </div>
      {withSmiles.length === 0 ? (
        <p className="text-[11px] text-slate-500">该配方暂无可视化的分子结构（缺少 SMILES）。</p>
      ) : (
        <>
          <p className="text-[11px] text-slate-500 mb-2">
            将通过 3Dmol.js 渲染以下 {withSmiles.length} 个组分的球棍模型：
          </p>
          <div className="flex flex-wrap gap-1">
            {withSmiles.map((e) => (
              <span
                key={e.name}
                title={e.smiles ?? ""}
                className="text-[10px] bg-edge px-1.5 py-0.5 rounded text-slate-300 font-mono"
              >
                {e.name}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

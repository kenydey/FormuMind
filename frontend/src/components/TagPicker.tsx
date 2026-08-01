import { useState } from "react";

const PRESET_TAGS = ["验证批次", "客户样件", "待复现", "失败", "最佳候选", "预混料异常", "固化异常"];

interface TagPickerProps {
  initialTags: string[];
  onSave: (tags: string[]) => void;
  onCancel: () => void;
}

export default function TagPicker({ initialTags, onSave, onCancel }: TagPickerProps) {
  const [selected, setSelected] = useState<string[]>(initialTags);
  const [customInput, setCustomInput] = useState("");

  const toggle = (tag: string) => {
    setSelected((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const addCustom = () => {
    const trimmed = customInput.trim();
    if (trimmed && !selected.includes(trimmed)) {
      setSelected((prev) => [...prev, trimmed]);
      setCustomInput("");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onCancel}>
      <div className="bg-gray-800 border border-edge rounded-lg shadow-xl p-4 w-80 max-w-[90vw]" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-xs font-semibold text-slate-200 mb-2">标记标签</h3>
        <div className="flex flex-wrap gap-1.5 mb-3">
          {PRESET_TAGS.map((tag) => (
            <button
              key={tag}
              onClick={() => toggle(tag)}
              className={`text-[10px] px-2 py-0.5 rounded border font-medium transition-colors ${
                selected.includes(tag)
                  ? "bg-accent2/30 text-accent2 border-accent2/50"
                  : "text-slate-400 border-edge hover:text-slate-200"
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5">
          <input
            className="flex-1 text-[10px] bg-gray-900 border border-edge rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-accent2"
            value={customInput}
            onChange={(e) => setCustomInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addCustom()}
            placeholder="自定义标签…"
          />
          <button onClick={addCustom} className="text-[10px] border border-edge rounded px-2 py-1 text-slate-400 hover:text-slate-200">添加</button>
        </div>
        <div className="flex justify-end gap-2 mt-3">
          <button onClick={onCancel} className="text-xs text-slate-400 px-2 py-1 hover:text-slate-200">取消</button>
          <button onClick={() => onSave(selected)} className="text-xs bg-accent2/90 hover:bg-accent2 text-ink rounded px-3 py-1 font-semibold">确定</button>
        </div>
      </div>
    </div>
  );
}

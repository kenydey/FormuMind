import { useState } from "react";

interface NoteEditorProps {
  initialNote: string;
  onSave: (note: string) => void;
  onCancel: () => void;
}

export default function NoteEditor({ initialNote, onSave, onCancel }: NoteEditorProps) {
  const [text, setText] = useState(initialNote);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onCancel}>
      <div className="bg-gray-800 border border-edge rounded-lg shadow-xl p-4 w-96 max-w-[90vw]" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-xs font-semibold text-slate-200 mb-2">实验备注</h3>
        <textarea
          className="w-full h-28 text-xs bg-gray-900 border border-edge rounded px-2 py-1.5 text-slate-200 resize-none focus:outline-none focus:border-accent2"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="记录操作偏差、异常观察、参数调整原因…"
        />
        <div className="flex justify-end gap-2 mt-2">
          <button onClick={onCancel} className="text-xs text-slate-400 px-2 py-1 hover:text-slate-200">取消</button>
          <button onClick={() => onSave(text)} className="text-xs bg-accent2/90 hover:bg-accent2 text-ink rounded px-3 py-1 font-semibold">保存</button>
        </div>
      </div>
    </div>
  );
}

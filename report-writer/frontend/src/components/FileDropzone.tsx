// "use client";

// import { useRef, useState } from "react";

// type Props = {
//   label: string;
//   hint: string;
//   accept: string;
//   file: File | null;
//   onChange: (file: File | null) => void;
//   accentColor: string;
// };

// export default function FileDropzone({ label, hint, accept, file, onChange, accentColor }: Props) {
//   const inputRef = useRef<HTMLInputElement>(null);
//   const [dragOver, setDragOver] = useState(false);

//   return (
//     <div>
//       <div className="flex items-baseline justify-between mb-1.5">
//         <span className="text-sm font-medium text-neutral-800">{label}</span>
//         {file && (
//           <button
//             type="button"
//             onClick={() => onChange(null)}
//             className="text-xs text-neutral-400 hover:text-neutral-600"
//           >
//             remove
//           </button>
//         )}
//       </div>
//       <div
//         onClick={() => inputRef.current?.click()}
//         onDragOver={(e) => {
//           e.preventDefault();
//           setDragOver(true);
//         }}
//         onDragLeave={() => setDragOver(false)}
//         onDrop={(e) => {
//           e.preventDefault();
//           setDragOver(false);
//           const dropped = e.dataTransfer.files?.[0];
//           if (dropped) onChange(dropped);
//         }}
//         className="cursor-pointer rounded-lg border-2 border-dashed px-4 py-5 text-center transition-colors"
//         style={{
//           borderColor: dragOver ? accentColor : file ? "#c3c2b7" : "#e1e0d9",
//           backgroundColor: file ? "#fcfcfb" : dragOver ? "#fcfcfb" : "transparent",
//         }}
//       >
//         <input
//           ref={inputRef}
//           type="file"
//           accept={accept}
//           className="hidden"
//           onChange={(e) => onChange(e.target.files?.[0] ?? null)}
//         />
//         {file ? (
//           <div className="text-sm text-neutral-800 font-medium truncate">{file.name}</div>
//         ) : (
//           <>
//             <div className="text-sm text-neutral-600">Drop file or click to browse</div>
//             <div className="text-xs text-neutral-400 mt-0.5">{hint}</div>
//           </>
//         )}
//       </div>
//     </div>
//   );
// }


"use client";

import { useCallback, useId, useRef, useState } from "react";

const MAX_SIZE_BYTES = 20 * 1024 * 1024; // 20 MB

type Props = {
  label: string;
  hint: string;
  /** Comma-separated extensions, e.g. ".csv" or ".xlsx,.xls,.csv" */
  accept: string;
  file: File | null;
  onChange: (file: File | null) => void;
  accentColor: string;
  disabled?: boolean;
};

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileDropzone({
  label,
  hint,
  accept,
  file,
  onChange,
  accentColor,
  disabled = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  // drag counter avoids flicker when dragging over child elements
  const dragDepth = useRef(0);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const labelId = useId();
  const errorId = useId();

  const allowedExts = accept
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);

  const validateAndSet = useCallback(
    (candidate: File) => {
      const ext = `.${candidate.name.split(".").pop()?.toLowerCase() ?? ""}`;
      if (!allowedExts.includes(ext)) {
        setError(`Unsupported file type. Expected: ${allowedExts.join(", ")}`);
        return;
      }
      if (candidate.size === 0) {
        setError("That file is empty.");
        return;
      }
      if (candidate.size > MAX_SIZE_BYTES) {
        setError(
          `File is ${formatSize(candidate.size)} — max is ${formatSize(MAX_SIZE_BYTES)}.`
        );
        return;
      }
      setError(null);
      onChange(candidate);
    },
    [allowedExts, onChange]
  );

  const openPicker = () => {
    if (!disabled) inputRef.current?.click();
  };

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span id={labelId} className="text-sm font-medium text-ink">
          {label}
        </span>
        {file && !disabled && (
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setError(null);
              if (inputRef.current) inputRef.current.value = "";
            }}
            className="rounded text-xs font-medium text-ink-muted transition-colors hover:text-ink-secondary focus:outline-none focus-visible:ring-2 focus-visible:ring-neutral-400"
          >
            Remove
          </button>
        )}
      </div>

      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-labelledby={labelId}
        aria-describedby={error ? errorId : undefined}
        aria-disabled={disabled}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          if (disabled) return;
          dragDepth.current++;
          setDragOver(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault();
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragOver(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragOver(false);
          if (disabled) return;
          const dropped = e.dataTransfer.files?.[0];
          if (dropped) validateAndSet(dropped);
        }}
        className={`flex items-center gap-3 rounded-xl border-2 border-dashed px-4 py-3.5 text-left transition-all focus:outline-none focus-visible:ring-4 focus-visible:ring-offset-1 ${
          disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
        }`}
        style={{
          borderColor: error
            ? "#d03b3b"
            : dragOver
              ? accentColor
              : file
                ? "#c3c2b7"
                : "#e1e0d9",
          backgroundColor: dragOver ? `${accentColor}0d` : file ? "#fcfcfb" : "transparent",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          disabled={disabled}
          className="hidden"
          onChange={(e) => {
            const picked = e.target.files?.[0];
            if (picked) validateAndSet(picked);
            // allow re-selecting the same file after removal
            e.target.value = "";
          }}
        />
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: file ? `${accentColor}1a` : "#f1f0eb", color: file ? accentColor : "#898781" }}
        >
          {file ? (
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
              <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
            </svg>
          ) : (
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
              <path d="M9.25 13.25a.75.75 0 001.5 0V4.636l2.955 3.129a.75.75 0 001.09-1.03l-4.25-4.5a.75.75 0 00-1.09 0l-4.25 4.5a.75.75 0 101.09 1.03L9.25 4.636v8.614z" />
              <path d="M3.5 12.75a.75.75 0 00-1.5 0v2.5A2.75 2.75 0 004.75 18h10.5A2.75 2.75 0 0018 15.25v-2.5a.75.75 0 00-1.5 0v2.5c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25v-2.5z" />
            </svg>
          )}
        </span>
        {file ? (
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-ink">
              {file.name}
            </div>
            <div className="mt-0.5 text-xs text-ink-muted">
              {formatSize(file.size)} · click to replace
            </div>
          </div>
        ) : (
          <div className="min-w-0">
            <div className="text-sm text-ink-secondary">
              Drop file or click to browse
            </div>
            <div className="mt-0.5 text-xs text-ink-muted">{hint}</div>
          </div>
        )}
      </div>

      {error && (
        <p id={errorId} role="alert" className="mt-1.5 text-xs font-medium text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}
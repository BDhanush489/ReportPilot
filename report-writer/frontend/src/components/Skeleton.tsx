"use client";

/** Generic shimmering placeholder block. */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-md bg-neutral-200/70 motion-reduce:animate-none ${className}`}
    />
  );
}

/** Skeleton mimicking the report preview layout, shown while generating. */
export function ReportSkeleton() {
  return (
    <div aria-hidden className="space-y-5">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <Skeleton className="h-5 w-64" />
          <Skeleton className="h-3 w-40" />
        </div>
        <Skeleton className="h-8 w-32" />
      </div>
      <Skeleton className="h-16 w-full" />
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
          <Skeleton className="h-3 w-4/6" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-5/6" />
        </div>
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-11/12" />
          <Skeleton className="h-3 w-3/4" />
        </div>
      ))}
    </div>
  );
}

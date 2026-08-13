import { API_BASE_URL, type HealthResponse } from "@/lib/api";

// Phase 1 is a stack check, not the product surface. The paste-and-analyze
// flow lands in Phase 4 and replaces this page.
export const dynamic = "force-dynamic";

async function loadHealth(): Promise<HealthResponse | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/health`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-success" : "bg-destructive"}`}
    />
  );
}

export default async function Home() {
  const health = await loadHealth();
  const components = Object.entries(health?.components ?? {});

  return (
    <main id="main" className="mx-auto max-w-2xl px-6 py-24">
      <h1 className="text-2xl font-semibold tracking-tight">SocialDownloader</h1>
      <p className="mt-3 text-muted-foreground">
        Extraction layer for publicly accessible social media posts — captions,
        thumbnails, subtitles, metadata and transcripts.
      </p>

      <section aria-labelledby="status-heading" className="mt-12">
        <h2
          id="status-heading"
          className="text-xs font-medium uppercase tracking-widest text-muted-foreground"
        >
          Stack status
        </h2>

        <dl className="mt-4 divide-y divide-border rounded-lg border border-border">
          <div className="flex items-center justify-between px-4 py-3">
            <dt className="text-sm">API</dt>
            <dd className="flex items-center gap-2 text-sm text-muted-foreground">
              <StatusDot ok={health !== null} />
              {health ? `reachable · v${health.version}` : "unreachable"}
            </dd>
          </div>

          {components.map(([name, component]) => (
            <div
              key={name}
              className="flex items-center justify-between px-4 py-3"
            >
              <dt className="text-sm capitalize">{name}</dt>
              <dd className="flex items-center gap-2 text-sm text-muted-foreground">
                <StatusDot ok={component.status === "ok"} />
                {component.status}
                {component.latency_ms !== null
                  ? ` · ${component.latency_ms.toFixed(1)}ms`
                  : ""}
              </dd>
            </div>
          ))}
        </dl>

        {health === null ? (
          <p className="mt-4 text-sm text-muted-foreground">
            The API isn&apos;t responding at{" "}
            <code className="font-mono text-xs">{API_BASE_URL}</code>. Start the
            stack with <code className="font-mono text-xs">docker compose up</code>.
          </p>
        ) : null}
      </section>

      <footer className="mt-16 border-t border-border pt-6 text-xs leading-relaxed text-muted-foreground">
        Public content only. You are responsible for respecting copyright and
        each platform&apos;s terms of service. Compliance pages ship in Phase 8.
      </footer>
    </main>
  );
}

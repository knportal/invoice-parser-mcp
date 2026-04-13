/**
 * Invoice Parser MCP — Cloudflare Worker
 *
 * Serves the landing page at / and proxies all other requests to the
 * Invoice Parser server running on Railway.
 */

const LANDING_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Invoice Parser MCP — Parse Any Invoice from Any AI Agent</title>
  <meta name="description" content="Invoice Parser MCP lets AI agents extract structured data from any invoice PDF or image — vendor, amount, line items, dates — with a single tool call. Pay per use." />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0a0a0f;
      --surface: #12121a;
      --border: #1e1e2e;
      --accent: #7c6fcd;
      --accent-light: #a89be0;
      --text: #e8e8f0;
      --muted: #8888aa;
      --green: #4ade80;
      --mono: 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      line-height: 1.6;
    }

    a { color: var(--accent-light); text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* NAV */
    nav {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 1.25rem 2rem;
      border-bottom: 1px solid var(--border);
    }
    .logo { font-weight: 700; font-size: 1.05rem; letter-spacing: -0.02em; }
    .logo span { color: var(--accent-light); }
    .nav-links { display: flex; gap: 2rem; font-size: 0.9rem; color: var(--muted); }
    .nav-links a { color: var(--muted); }
    .nav-links a:hover { color: var(--text); }
    .nav-cta {
      background: var(--accent);
      color: #fff;
      padding: 0.45rem 1.1rem;
      border-radius: 6px;
      font-size: 0.9rem;
      font-weight: 600;
    }
    .nav-cta:hover { background: var(--accent-light); text-decoration: none; }

    /* HERO */
    .hero {
      max-width: 760px;
      margin: 5rem auto 4rem;
      padding: 0 2rem;
      text-align: center;
    }
    .hero-badge {
      display: inline-block;
      background: rgba(124,111,205,0.15);
      border: 1px solid rgba(124,111,205,0.3);
      color: var(--accent-light);
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 0.3rem 0.9rem;
      border-radius: 100px;
      margin-bottom: 1.5rem;
    }
    h1 {
      font-size: clamp(2rem, 5vw, 3.2rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 1.15;
      margin-bottom: 1.25rem;
    }
    h1 em { font-style: normal; color: var(--accent-light); }
    .hero-sub {
      font-size: 1.15rem;
      color: var(--muted);
      max-width: 560px;
      margin: 0 auto 2.5rem;
    }
    .hero-actions { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
    .btn-primary {
      background: var(--accent);
      color: #fff;
      padding: 0.75rem 1.75rem;
      border-radius: 8px;
      font-weight: 700;
      font-size: 1rem;
    }
    .btn-primary:hover { background: var(--accent-light); text-decoration: none; }
    .btn-secondary {
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 0.75rem 1.75rem;
      border-radius: 8px;
      font-size: 1rem;
    }
    .btn-secondary:hover { border-color: var(--accent); color: var(--text); text-decoration: none; }

    /* CODE BLOCK */
    .code-preview {
      max-width: 700px;
      margin: 3rem auto 5rem;
      padding: 0 2rem;
    }
    .code-block {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }
    .code-header {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.75rem 1.25rem;
      border-bottom: 1px solid var(--border);
      font-size: 0.8rem;
      color: var(--muted);
    }
    .dot { width: 10px; height: 10px; border-radius: 50%; }
    .dot-r { background: #ff5f57; }
    .dot-y { background: #febc2e; }
    .dot-g { background: #28c840; }
    .code-body {
      padding: 1.5rem;
      font-family: var(--mono);
      font-size: 0.82rem;
      line-height: 1.7;
      overflow-x: auto;
    }
    .kw { color: #c792ea; }
    .str { color: #c3e88d; }
    .key { color: #82aaff; }
    .val { color: #f78c6c; }
    .cmt { color: #546e7a; }

    /* USE CASES */
    section { max-width: 900px; margin: 0 auto; padding: 4rem 2rem; }
    h2 { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.5rem; }
    .section-sub { color: var(--muted); margin-bottom: 3rem; }

    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.25rem; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
    }
    .card-icon { font-size: 1.6rem; margin-bottom: 0.75rem; }
    .card h3 { font-size: 1rem; font-weight: 700; margin-bottom: 0.5rem; }
    .card p { font-size: 0.9rem; color: var(--muted); }

    /* PRICING */
    .pricing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.5rem; }
    .plan {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 2rem;
    }
    .plan.featured { border-color: var(--accent); }
    .plan-badge {
      display: inline-block;
      background: var(--accent);
      color: #fff;
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
      margin-bottom: 1rem;
    }
    .plan-name { font-size: 1.1rem; font-weight: 700; margin-bottom: 0.25rem; }
    .plan-price { font-size: 2.5rem; font-weight: 800; letter-spacing: -0.04em; }
    .plan-price span { font-size: 1rem; font-weight: 400; color: var(--muted); }
    .plan-desc { color: var(--muted); font-size: 0.9rem; margin: 0.75rem 0 1.5rem; }
    .plan-features { list-style: none; }
    .plan-features li {
      font-size: 0.9rem;
      color: var(--muted);
      padding: 0.4rem 0;
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .plan-features li::before { content: '✓'; color: var(--green); font-weight: 700; }
    .plan-cta {
      display: block;
      text-align: center;
      margin-top: 1.75rem;
      padding: 0.7rem;
      border-radius: 8px;
      font-weight: 700;
      font-size: 0.95rem;
    }
    .plan-cta-outline {
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .plan-cta-outline:hover { border-color: var(--accent); color: var(--text); text-decoration: none; }
    .plan-cta-filled { background: var(--accent); color: #fff; }
    .plan-cta-filled:hover { background: var(--accent-light); text-decoration: none; }

    /* DIVIDER */
    hr { border: none; border-top: 1px solid var(--border); }

    /* FOOTER */
    footer {
      text-align: center;
      padding: 2.5rem 2rem;
      font-size: 0.85rem;
      color: var(--muted);
    }
    footer a { color: var(--muted); }
    footer a:hover { color: var(--text); }
  </style>
</head>
<body>

  <nav>
    <div class="logo">Invoice Parser <span>MCP</span></div>
    <div class="nav-links">
      <a href="#use-cases">Use Cases</a>
      <a href="#pricing">Pricing</a>
      <a href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Docs</a>
    </div>
    <a class="nav-cta" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Get Started</a>
  </nav>

  <div class="hero">
    <div class="hero-badge">MCP Server · Pay Per Use · Plenitudo.ai</div>
    <h1>Parse any invoice from <em>any AI agent</em></h1>
    <p class="hero-sub">
      PDF or image URL → structured JSON in one tool call. Vendor, amount, line items, dates — extracted by Claude Vision. No signup, no subscription.
    </p>
    <div class="hero-actions">
      <a class="btn-primary" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">View on GitHub</a>
      <a class="btn-secondary" href="https://smithery.ai" target="_blank">View on Smithery</a>
    </div>
  </div>

  <div class="code-preview">
    <div class="code-block">
      <div class="code-header">
        <div class="dot dot-r"></div>
        <div class="dot dot-y"></div>
        <div class="dot dot-g"></div>
        <span style="margin-left:0.5rem">Example tool call</span>
      </div>
      <div class="code-body"><pre><span class="cmt">// Agent parses an invoice in one call</span>
{
  <span class="key">"tool"</span>: <span class="str">"parse_invoice"</span>,
  <span class="key">"parameters"</span>: {
    <span class="key">"source"</span>: <span class="str">"https://example.com/invoice_2024_001.pdf"</span>
  }
}

<span class="cmt">// Response — structured JSON, ready to use</span>
{
  <span class="key">"vendor"</span>: <span class="str">"Acme Corp"</span>,
  <span class="key">"invoice_number"</span>: <span class="str">"INV-2024-001"</span>,
  <span class="key">"date"</span>: <span class="str">"2024-03-15"</span>,
  <span class="key">"total"</span>: <span class="val">1250.00</span>,
  <span class="key">"currency"</span>: <span class="str">"USD"</span>,
  <span class="key">"line_items"</span>: [
    { <span class="key">"description"</span>: <span class="str">"Consulting Services"</span>, <span class="key">"amount"</span>: <span class="val">1250.00</span> }
  ]
}</pre>
      </div>
    </div>
  </div>

  <section id="use-cases">
    <h2>Built for agent workflows</h2>
    <p class="section-sub">Wherever an agent needs to read and act on invoice data, Invoice Parser handles the extraction.</p>
    <div class="cards">
      <div class="card">
        <div class="card-icon">💼</div>
        <h3>Accounts Payable</h3>
        <p>Agents receive supplier invoices, extract all fields automatically, and push to your ERP or accounting system without human review.</p>
      </div>
      <div class="card">
        <div class="card-icon">📊</div>
        <h3>Expense Reconciliation</h3>
        <p>Upload a folder of receipts and invoices — the agent extracts every amount, vendor, and date and matches them against your records.</p>
      </div>
      <div class="card">
        <div class="card-icon">🔍</div>
        <h3>Audit & Compliance</h3>
        <p>Structured invoice data makes it trivial to flag duplicates, verify tax amounts, and ensure vendor details match approved lists.</p>
      </div>
      <div class="card">
        <div class="card-icon">🏗️</div>
        <h3>Construction & Contractors</h3>
        <p>Parse subcontractor invoices, extract line items by job code, and feed directly into project cost tracking — all from a single tool call.</p>
      </div>
    </div>
  </section>

  <hr />

  <section id="pricing">
    <h2>Pay per use</h2>
    <p class="section-sub">No subscription. No API key. Just call the tool — payment handled via x402 micropayments.</p>
    <div class="pricing-grid">
      <div class="plan">
        <div class="plan-name">Per Call</div>
        <div class="plan-price">$0.02 <span>/ invoice</span></div>
        <div class="plan-desc">Charged automatically via x402 micropayments. No account required.</div>
        <ul class="plan-features">
          <li>PDF and image URLs supported</li>
          <li>Claude Vision extraction</li>
          <li>Vendor, amount, line items, dates</li>
          <li>USDC on Base (automatic)</li>
          <li>Works with any MCP client</li>
        </ul>
        <a class="plan-cta plan-cta-outline" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Read the Docs</a>
      </div>
      <div class="plan featured">
        <div class="plan-badge">MCP Endpoint</div>
        <div class="plan-name">Ready to Use</div>
        <div class="plan-price" style="font-size:1.1rem; padding-top:0.5rem; word-break:break-all; color: var(--accent-light);">invoice-parser.plenitudo.ai/mcp</div>
        <div class="plan-desc" style="margin-top:1rem;">Add to any MCP-compatible agent and start parsing invoices immediately.</div>
        <ul class="plan-features">
          <li>No signup required</li>
          <li>Live on Railway + Cloudflare</li>
          <li>Listed on Smithery &amp; Glama</li>
          <li>Open source on GitHub</li>
        </ul>
        <a class="plan-cta plan-cta-filled" href="https://smithery.ai" target="_blank">View on Smithery</a>
      </div>
    </div>
  </section>

  <hr />

  <section id="connect" style="text-align:center; padding: 5rem 2rem;">
    <h2>Connect your agent</h2>
    <p class="section-sub" style="margin-bottom:2rem;">Add this endpoint to any MCP-compatible AI client to start parsing invoices.</p>
    <div style="border: 1px solid var(--border); border-radius: 10px; padding: 1.5rem; max-width: 520px; margin: 0 auto; text-align:left;">
      <p style="font-size: 0.8rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 0.75rem;">MCP Endpoint</p>
      <code style="font-family: var(--mono); font-size: 0.85rem; color: var(--accent-light); word-break: break-all;">https://invoice-parser.plenitudo.ai/mcp</code>
    </div>
  </section>

  <footer>
    <p>
      Built by <a href="https://plenitudo.ai" target="_blank">Plenitudo.ai</a> &nbsp;·&nbsp;
      <a href="https://smithery.ai" target="_blank">Smithery</a> &nbsp;·&nbsp;
      <a href="https://github.com/knportal/invoice-parser-mcp" target="_blank">GitHub</a>
    </p>
    <p style="margin-top:0.5rem; font-size: 0.8rem;">© 2026 Plenitudo · Invoice Parser MCP is powered by Claude Vision (Anthropic).</p>
  </footer>

</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Serve landing page at root
    if (url.pathname === "/" || url.pathname === "") {
      return new Response(LANDING_HTML, {
        headers: { "Content-Type": "text/html;charset=UTF-8" },
      });
    }

    const originUrl = env.INVOICE_PARSER_ORIGIN_URL;

    if (!originUrl) {
      return new Response(
        JSON.stringify({ error: "INVOICE_PARSER_ORIGIN_URL secret is not configured." }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }

    // Build the forwarded URL — preserve path and query string
    const targetUrl = new URL(url.pathname + url.search, originUrl);

    // Forward the request, preserving method, headers, and body
    const forwardedRequest = new Request(targetUrl.toString(), {
      method: request.method,
      headers: request.headers,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? request.body
        : undefined,
    });

    try {
      const response = await fetch(forwardedRequest);
      return response;
    } catch (err) {
      return new Response(
        JSON.stringify({ error: `Failed to reach Invoice Parser origin: ${err.message}` }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }
  },
};

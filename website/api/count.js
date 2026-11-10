// Total downloads = GitHub release downloads (history, plus anyone using the GitHub link)
// + downloads counted by /api/download. Cached at the edge for a minute so GitHub's
// 60-requests-per-hour API limit is never hit.
const KEY = 'dotix_downloads';
const LEGACY_KEY = 'pm_downloads';
const RELEASES = 'https://api.github.com/repos/sandeepm13/prompt-master/releases';

const redisUrl = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const redisToken = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;

async function githubTotal() {
  try {
    const res = await fetch(RELEASES, {
      headers: { Accept: 'application/vnd.github.v3+json', 'User-Agent': 'dotix-site' },
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    let total = 0;
    (await res.json()).forEach((release) => {
      (release.assets || []).forEach((asset) => {
        if (asset.name && asset.name.endsWith('.exe')) total += asset.download_count || 0;
      });
    });
    return total;
  } catch (e) {
    return null;
  }
}

async function counted() {
  if (!redisUrl || !redisToken) return 0;
  try {
    const fetchVal = async (k) => {
      const res = await fetch(`${redisUrl}/get/${k}`, {
        headers: { Authorization: `Bearer ${redisToken}` },
        signal: AbortSignal.timeout(1500),
      });
      if (!res.ok) return 0;
      return parseInt((await res.json()).result || '0', 10) || 0;
    };
    const [c1, c2] = await Promise.all([fetchVal(KEY), fetchVal(LEGACY_KEY)]);
    return c1 + c2;
  } catch (e) {
    return null;
  }
}

module.exports = async (req, res) => {
  const [github, vercel] = await Promise.all([githubTotal(), counted()]);
  res.setHeader('Content-Type', 'application/json');
  // A partial total would make the number jump backwards; let the page keep its last value instead.
  if (github === null || vercel === null) {
    res.statusCode = 503;
    res.setHeader('Cache-Control', 'no-store');
    res.end(JSON.stringify({ error: 'count unavailable' }));
    return;
  }
  res.statusCode = 200;
  res.setHeader('Cache-Control', 'public, s-maxage=60, stale-while-revalidate=600');
  res.end(JSON.stringify({ count: github + vercel, github, vercel }));
};

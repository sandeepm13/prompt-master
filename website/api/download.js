// Counts one download, then sends the browser to the latest release on GitHub,
// falling back to the bundled Vercel file if GitHub API is unreachable or rate-limited.
const REPO = 'sandeepm13/prompt-master';
const FALLBACK_FILE = '/dotix-Setup-2.1.0.exe';
const KEY = 'dotix_downloads';

const redisUrl = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const redisToken = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;

const isBot = (ua) => !ua || /bot|crawl|spider|slurp|preview|facebookexternalhit|curl|wget/i.test(ua);

async function getLatestDownloadUrl() {
  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { Accept: 'application/vnd.github.v3+json', 'User-Agent': 'dotix-site' },
      signal: AbortSignal.timeout(2000),
    });
    if (res.ok) {
      const data = await res.json();
      const exeAsset = (data.assets || []).find((a) => a.name && a.name.endsWith('.exe'));
      if (exeAsset && exeAsset.browser_download_url) {
        return exeAsset.browser_download_url;
      }
    }
  } catch (e) {}
  return FALLBACK_FILE;
}

module.exports = async (req, res) => {
  if (req.method === 'GET' && redisUrl && redisToken && !isBot(req.headers['user-agent'])) {
    try {
      await fetch(`${redisUrl}/incr/${KEY}`, {
        headers: { Authorization: `Bearer ${redisToken}` },
        signal: AbortSignal.timeout(1500),
      });
    } catch (e) {}
  }
  const targetUrl = await getLatestDownloadUrl();
  res.statusCode = 302;
  res.setHeader('Location', targetUrl);
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex');
  res.end();
};

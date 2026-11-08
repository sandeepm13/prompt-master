// Counts one download, then sends the browser to the installer served by Vercel.
// The download never waits on the counter: if Redis is missing, slow or down, it still redirects.
const FILE = '/MasterPrompt-Setup-2.0.0.exe';
const KEY = 'pm_downloads';

const redisUrl = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const redisToken = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;

const isBot = (ua) => !ua || /bot|crawl|spider|slurp|preview|facebookexternalhit|curl|wget/i.test(ua);

module.exports = async (req, res) => {
  if (req.method === 'GET' && redisUrl && redisToken && !isBot(req.headers['user-agent'])) {
    try {
      await fetch(`${redisUrl}/incr/${KEY}`, {
        headers: { Authorization: `Bearer ${redisToken}` },
        signal: AbortSignal.timeout(1500),
      });
    } catch (e) {}
  }
  res.statusCode = 302;
  res.setHeader('Location', FILE);
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex');
  res.end();
};

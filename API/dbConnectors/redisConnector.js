const redis = require('redis');

const MAX_RETRY_ATTEMPTS = 20;
const MAX_RETRY_DELAY_MS = 30000; // 30 s

function reconnectStrategy(retries) {
  if (retries >= MAX_RETRY_ATTEMPTS) {
    return new Error(`Redis: giving up after ${MAX_RETRY_ATTEMPTS} reconnection attempts`);
  }
  // Exponential backoff: 200ms * 2^retries, capped at MAX_RETRY_DELAY_MS.
  const delay = Math.min(200 * Math.pow(2, retries), MAX_RETRY_DELAY_MS);
  console.error(`Redis: connection lost. Retrying in ${delay}ms (attempt ${retries + 1}/${MAX_RETRY_ATTEMPTS})`);
  return delay;
}

let host = 'localhost';
if (process.env.Docker_ENV === 'True') {
  host = '172.29.0.4';
}

const redisClient = redis.createClient({
  socket: {
    host,
    port: 6379,
    reconnectStrategy,
  },
});

redisClient.on('error', function (error) {
  console.error('Redis error:', error);
});

redisClient.on('reconnecting', function () {
  console.error('Redis: reconnecting...');
});

redisClient.connect();

module.exports = redisClient;

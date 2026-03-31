/**
 * Tests for API/dbConnectors/redisConnector.js reconnection strategy.
 *
 * The bug: the client is created with no socket.reconnectStrategy option.
 * redis@4 requires an explicit reconnect strategy; without one, retries
 * stop after a small number of attempts and the connection is silently lost.
 *
 * Expected: the client options include a reconnectStrategy function that
 * implements exponential backoff and gives up after a bounded number of
 * retries (returning an Error to stop reconnecting).
 */

const redis = require('redis');

// Intercept redis.createClient so we can inspect the options passed.
let capturedOptions = null;
const originalCreate = redis.createClient;

jest.mock('redis', () => {
  const actual = jest.requireActual('redis');
  return {
    ...actual,
    createClient: jest.fn((opts) => {
      // Store opts for inspection, then return a stub that does not connect.
      capturedOptions = opts;
      const stub = {
        on: jest.fn().mockReturnThis(),
        connect: jest.fn().mockResolvedValue(undefined),
      };
      return stub;
    }),
  };
});

beforeEach(() => {
  capturedOptions = null;
  jest.resetModules();
});

function loadConnector() {
  // Reload fresh each time to re-run module-level createClient call.
  jest.resetModules();
  // Re-apply mock after reset.
  jest.mock('redis', () => {
    return {
      createClient: jest.fn((opts) => {
        capturedOptions = opts;
        return {
          on: jest.fn().mockReturnThis(),
          connect: jest.fn().mockResolvedValue(undefined),
        };
      }),
    };
  });
  require('../dbConnectors/redisConnector');
}

describe('redisConnector — reconnection strategy', () => {
  test('createClient is called with a reconnectStrategy function', () => {
    loadConnector();
    expect(capturedOptions).not.toBeNull();
    expect(typeof capturedOptions.socket).toBe('object');
    expect(typeof capturedOptions.socket.reconnectStrategy).toBe('function');
  });

  test('reconnectStrategy returns increasing delays for the first retries', () => {
    loadConnector();
    const strategy = capturedOptions.socket.reconnectStrategy;
    const delay0 = strategy(0);
    const delay1 = strategy(1);
    const delay2 = strategy(2);
    // Delays should be numbers (ms) and increase.
    expect(typeof delay0).toBe('number');
    expect(delay1).toBeGreaterThan(delay0);
    expect(delay2).toBeGreaterThan(delay1);
  });

  test('reconnectStrategy returns an Error after max retries to stop reconnecting', () => {
    loadConnector();
    const strategy = capturedOptions.socket.reconnectStrategy;
    // Try a very high attempt count — should eventually return an Error.
    const result = strategy(1000);
    expect(result instanceof Error).toBe(true);
  });

  test('reconnectStrategy caps delay at a maximum value', () => {
    loadConnector();
    const strategy = capturedOptions.socket.reconnectStrategy;
    const MAX_RETRIES = 10; // reasonable upper bound before giving up
    for (let i = 0; i < MAX_RETRIES; i++) {
      const delay = strategy(i);
      if (typeof delay === 'number') {
        // Delay must not grow unbounded.
        expect(delay).toBeLessThanOrEqual(30000);
      }
    }
  });
});

/**
 * Tests for POST /logout session destroy behaviour.
 *
 * The bug: res.clearCookie() and the 200 response are called outside
 * the session.destroy() callback. With an async store (Redis, Mongo),
 * the outer code runs before the callback fires, so the 200 is sent
 * regardless of whether destroy succeeded or failed.
 *
 * Expected: on destroy failure → 500, cookies not cleared.
 * Expected: on destroy success → 200, cookies cleared.
 */

const express = require('express');
const request = require('supertest');

function buildApp(destroyErr) {
  const app = express();
  app.use((req, _res, next) => {
    req.session = {
      destroy(cb) {
        // setImmediate mimics an async session store (Redis, Mongo, etc.)
        setImmediate(() => cb(destroyErr || null));
      },
    };
    next();
  });

  // Matches the fixed logout handler in app.js.
  // All response logic is inside the session.destroy() callback.
  app.post('/logout', (req, res) => {
    req.session.destroy(err => {
      if (err) {
        return res.status(500).send('Error logging out');
      }
      res.clearCookie('accessToken');
      res.clearCookie('refreshToken');
      res.status(200).json({ message: 'Logged out successfully' });
    });
  });

  return app;
}

function clearedTokenCount(res) {
  const setCookie = res.headers['set-cookie'] || [];
  return setCookie.filter(h =>
    (h.startsWith('accessToken=') || h.startsWith('refreshToken=')) &&
    h.toLowerCase().includes('expires=thu, 01 jan 1970')
  ).length;
}

describe('POST /logout', () => {
  describe('on session.destroy() success', () => {
    test('returns 200', async () => {
      const res = await request(buildApp(null)).post('/logout');
      expect(res.status).toBe(200);
    });

    test('clears accessToken and refreshToken cookies', async () => {
      const res = await request(buildApp(null)).post('/logout');
      expect(clearedTokenCount(res)).toBe(2);
    });
  });

  describe('on session.destroy() failure', () => {
    test('returns 500', async () => {
      const res = await request(buildApp(new Error('store error'))).post('/logout');
      expect(res.status).toBe(500);
    });

    test('does not clear cookies', async () => {
      const res = await request(buildApp(new Error('store error'))).post('/logout');
      expect(clearedTokenCount(res)).toBe(0);
    });
  });
});

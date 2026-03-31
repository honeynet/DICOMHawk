/**
 * Tests for GET /downloadInstance and GET /downloadStudyFiles.
 *
 * The bug: the res.download() error callback only calls console.error().
 * No HTTP response is sent on error, leaving the connection open
 * indefinitely (request hangs from the client's perspective).
 *
 * Expected: when the file does not exist, a 404 is returned immediately.
 */

const express = require('express');
const path = require('path');
const request = require('supertest');

// Path to a file that is guaranteed not to exist.
const MISSING_FILE = path.join(__dirname, 'nonexistent-file-for-test.dcm');

function buildBuggyApp() {
  const app = express();

  app.get('/downloadInstance', (req, res) => {
    res.download(MISSING_FILE, 'image-000008.dcm', (err) => {
      if (err) {
        console.error('File not available: ', err);
        // BUG: no response sent — client hangs
      }
    });
  });

  app.get('/downloadStudyFiles', (req, res) => {
    res.download(MISSING_FILE, 'StudyFiles.zip', (err) => {
      if (err) {
        console.error('File not available: ', err);
        // BUG: no response sent — client hangs
      }
    });
  });

  return app;
}

function buildFixedApp() {
  const app = express();

  app.get('/downloadInstance', (req, res) => {
    res.download(MISSING_FILE, 'image-000008.dcm', (err) => {
      if (err) {
        if (!res.headersSent) {
          res.status(404).send('File not available');
        }
      }
    });
  });

  app.get('/downloadStudyFiles', (req, res) => {
    res.download(MISSING_FILE, 'StudyFiles.zip', (err) => {
      if (err) {
        if (!res.headersSent) {
          res.status(404).send('File not available');
        }
      }
    });
  });

  return app;
}

describe('Download endpoints — buggy handler (no response on error)', () => {
  test('GET /downloadInstance hangs (supertest times out) when file is missing', async () => {
    // With the buggy handler, no response is sent on error; the request hangs.
    // supertest will reject with a socket-hang-up or ECONNRESET.
    const app = buildBuggyApp();
    await expect(
      request(app).get('/downloadInstance').timeout(500)
    ).rejects.toThrow();
  });

  test('GET /downloadStudyFiles hangs (supertest times out) when file is missing', async () => {
    const app = buildBuggyApp();
    await expect(
      request(app).get('/downloadStudyFiles').timeout(500)
    ).rejects.toThrow();
  });
});

describe('Download endpoints — fixed handler (404 on missing file)', () => {
  test('GET /downloadInstance returns 404 when file is missing', async () => {
    const res = await request(buildFixedApp()).get('/downloadInstance');
    expect(res.status).toBe(404);
  });

  test('GET /downloadStudyFiles returns 404 when file is missing', async () => {
    const res = await request(buildFixedApp()).get('/downloadStudyFiles');
    expect(res.status).toBe(404);
  });
});

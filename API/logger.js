const fs = require("fs");
const path = require("path");
const threat_intelligence = require("./threatIntelligence");

const getLogsDir = () => {
  return process.env.Docker_ENV === "True" ? "/var/log/dicomhawk" : path.join(__dirname, "logs");
};

function getClientIp(req) {
  const ip =
    req.headers["x-forwarded-for"] ||
    req.connection.remoteAddress ||
    req.socket.remoteAddress ||
    "";
  return ip.includes("::ffff:") ? ip.replace("::ffff:", "") : ip;
}

async function isIpScanned(ip) {
  try {
    const logPath = path.join(getLogsDir(), "scanned_ips.log");
    if (!fs.existsSync(logPath)) {
      fs.writeFileSync(logPath, "");
    }
    const scannedIps = fs.readFileSync(logPath, "utf8");
    return scannedIps.includes(ip.toString());
  } catch (error) {
    console.error("Error reading scanned IPs:", error);
    return false;
  }
}

async function logEvent(event, req, parameter = "N/A") {
  try {
    const logsDir = getLogsDir();
    if (!fs.existsSync(logsDir)) {
      fs.mkdirSync(logsDir, { recursive: true });
    }

    var ip = "";
    var r_port = "";
    rep = parameter;
    if (event === "FileUploaded") {
      ip = req.headers["x-requestor-ip"];
      port = req.headers["port"];
    } else {
      ip = getClientIp(req);
      r_port = req.connection.remotePort;
    }

    const isScanned = await isIpScanned(ip);

    if (!isScanned) {
      try {
        let repu = await threat_intelligence.getReputationData(ip);
        if (repu) {
          fs.appendFileSync(
            path.join(logsDir, "reputation.log"),
            JSON.stringify(repu) + "\n"
          );
        }
      } catch (error) {
        console.error("Error getting reputation data:", error);
      }
      fs.appendFileSync(
        path.join(logsDir, "scanned_ips.log"),
        ip + "\n"
      );
    }

    const jsonObject = {
      ip: ip,
      timestamp: new Date().toISOString().slice(0, 19),
      messevent: event,
      sessionId:
        (process.env.SESSION_SECRET || "default").substring(0, 4) +
        Date.now().toString().substring(0, 10),
      port: r_port,
      report: rep,
      known_scanner: fs.readFileSync('./blackhole_list.txt', 'utf8').includes('exampleString')
    };

    const jsonString = JSON.stringify(jsonObject);
    fs.appendFileSync(
      path.join(logsDir, "api_logs.log"),
      jsonString + "\n"
    );
  } catch (error) {
    console.error("Error in logEvent:", error);
  }
}

module.exports = { logEvent };
 
const axios = require('axios');
const fs = require('fs');

/**
 * Read a Docker secret from a mounted file.
 * Falls back to null if the file does not exist, so callers
 * can chain with process.env for local development.
 *
 * The base path is set by SECRETS_BASE_PATH (default: /run/secrets),
 * keeping it compatible with Compose, Swarm, and bare-metal setups.
 */
function readSecret(name) {
    const base = process.env.SECRETS_BASE_PATH || "/run/secrets";
    try {
        return fs.readFileSync(`${base}/${name}`, "utf8").trim();
    } catch {
        return null;
    }
}
const { format } = require('date-fns');

async function getIPSecurityScore(ip) {
    const apiKey = readSecret("abuse_ip_key") || process.env.ABUSE_IP_API_KEY;
    const url = "https://api.abuseipdb.com/api/v2/check";
    const headers = {
        Accept: "application/json",
        Key: apiKey
    };
    const params = {
        ipAddress: ip,
        maxAgeInDays: 90
    };

    try {
        const response = await axios.get(url, { headers, params });
        const data = response.data.data;
        // console.log(data);
        const country = data.countryCode || 'N/A';
        const isp = data.isp || 'N/A';
        const abuseIPConfidenceScore = data.abuseConfidenceScore;
        return [isp, abuseIPConfidenceScore, country];
    } catch (error) {
        console.error(`An error occurred: ${error.response ? error.response.data.errors[0].detail : error.message}`);
    }
}

async function getIpqualityScore(ip) {
    const apiKey = readSecret("ip_quality_score_key") || process.env.IP_QUALITY_SCORE_API_KEY;
    const url = `https://ipqualityscore.com/api/json/ip/${apiKey}/${ip}`;

    try {
        const response = await axios.get(url);
        const data = response.data;
        // console.log(data);
        return [
            data.fraud_score || 'N/A',
            data.proxy || 'N/A',
            data.city || 'N/A',
            data.bot_status || 'N/A',
            data.vpn || 'N/A',
            data.latitude || "N/A",
            data.longitude || "N/A"
        ];
    } catch (error) {
        return { service: "IPQualityScore", error: error.message };
    }
}

async function getVirusTotalScore(ip) {
    const apiKey = readSecret("virus_total_key") || process.env.VIRUS_TOTAL_API_KEY;
    const url = `https://www.virustotal.com/api/v3/ip_addresses/${ip}`;
    const headers = { "x-apikey": apiKey };

    try {
        const response = await axios.get(url, { headers });
        const data = response.data.data;
        const resultCounts = {};
        Object.values(data.attributes.last_analysis_results).forEach(analysis => {
            const result = analysis.result;
            resultCounts[result] = (resultCounts[result] || 0) + 1;
        });
        return resultCounts;
    } catch (error) {
        console.error(`An error occurred: ${error.response ? error.response.data : error.message}`);
    }
}

async function getReputationData(ip) {
    const currentTime = format(new Date(), "yyyy-MM-dd'T'HH:mm:ss");
    const abusedb = await getIPSecurityScore(ip);
    const abusedbObject = abusedb || ["N/A", "N/A", "N/A"];

    const ipQuality = await getIpqualityScore(ip);
    const ipQualityScore = ipQuality || ["N/A", "N/A", "N/A", "N/A", "N/A", "N/A", "N/A"];

    const vt = await getVirusTotalScore(ip);
    const virusTotal = vt || {};

    try {
        let repData = {};
        repData.timestamp = currentTime;
        repData.virus_total_results = virusTotal;
        repData.ip = ip;
        repData.ip_quality_score = ipQualityScore[0];
        repData.proxy = ipQualityScore[1];
        repData.region = ipQualityScore[2];
        repData.vpn = ipQualityScore[4];
        repData.country = abusedbObject[2];
        repData.ISP = abusedbObject[0];
        repData.AbuseDBScore = abusedbObject[1];

        return repData;
    } catch (error) {
        console.error("Unexpected error while building IP reputation object", error);
    }
}



module.exports = { getReputationData };
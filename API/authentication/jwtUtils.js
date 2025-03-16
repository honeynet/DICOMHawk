require('dotenv').config();


//JWT handling
const jwt = require("jsonwebtoken");

const ACCESS_TOKEN_SECRET = process.env.ACCESS_TOKEN_SECRET;
const REFRESH_TOKEN_SECRET = process.env.REFRESH_TOKEN_SECRET;
const ADMIN_SECRET = process.env.ADMIN_SECRET;
const ADMIN_REFRESH_TOKEN_SECRET = process.env.ADMIN_REFRESH_TOKEN_SECRET;

// console.log('Access Token  Secret: ', process.env.ACCESS_TOKEN_SECRET);
// console.log('Refresh Token Secret: ', process.env.REFRESH_TOKEN_SECRET);
// console.log('Curious Admin Token: ', process.env.ADMIN_SECRET);
// console.log('Curious Admin Refresh Token: ', process.env.ADMIN_REFRESH_TOKEN_SECRET);

if (!ACCESS_TOKEN_SECRET || !REFRESH_TOKEN_SECRET || !ADMIN_SECRET) {
    throw new Error("No JWT secrets present");
}

// Generate Access Token (5 minutes)
const generateAccessToken = (payload) => {
    return jwt.sign(payload, ACCESS_TOKEN_SECRET, { expiresIn: "5m" });
};

//Generate Admin Token (5 minutes)
const generateAdminAccessToken = (payload) => {
    return jwt.sign(payload, ADMIN_SECRET, { expiresIn: "5m" });
};

// Generate Refresh Token (15 minutes)
const generateRefreshToken = (payload) => {
    return jwt.sign(payload, REFRESH_TOKEN_SECRET, { expiresIn: "15m" });
};

// Generate Admin Refresh Token
const generateAdminRefreshToken = (payload) => {
    return jwt.sign(payload, ADMIN_REFRESH_TOKEN_SECRET, { expiresIn: "15m" });
};

// Verify Access Token
const verifyAccessToken = (token) => {
    //if the curiousadmin token does not match CURIOUS_ADMIN_SECRET

    return jwt.verify(token, ACCESS_TOKEN_SECRET);
};

// Verify Admin Access Token
const verifyAdminAccessToken = (token) => {
    //if the curiousadmin token does not match CURIOUS_ADMIN_SECRET
    return jwt.verify(token, ADMIN_SECRET);
};



// Verify Refresh Token
const verifyRefreshToken = (token) => {
    return jwt.verify(token, REFRESH_TOKEN_SECRET);
};

module.exports = {
    generateAccessToken,
    generateRefreshToken,
    verifyAccessToken,
    verifyRefreshToken,
    generateAdminAccessToken,
    generateAdminRefreshToken,
    verifyAdminAccessToken
};

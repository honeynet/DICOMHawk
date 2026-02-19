const crypto = require("crypto");
const { generateAccessToken } = require("../authentication/jwtUtils");
//const csrfTokens = new Map();

const generateCSRFToken = (req,res,next)=>{
    const csrfToken = crypto.pseudoRandomBytes(32).toString("hex");
    //csrfTokens.set(csrfToken,true);
    res.cookie("csrfToken", csrfToken, {httpOnly:true});
    next();
};

const verifyCSRFToken = (req,res,next)=>{
    const csrfToken = req.headers["x-csrf-token"];

    if(!csrfToken || !csrfTokens.has(csrfToken)){
        return res.status(403).json({message:"Invalid CSRF token"});
    }
    csrfTokens.delete(csrfToken);
    next();
};

module.exports = {generateAccessToken, verifyCSRFToken};
const jwtUtils = require("./jwtUtils");

const authenticate = (req, res, next) => {
    const token = req.cookies.accessToken;
    if (!token) {
        //access token missing
        // console.log("Token missing");
        // return res.status(403).json({message:"Forbidden"});
        return res.redirect('/');
    }

    try {

        const decoded = jwtUtils.verifyAccessToken(token);

        req.username = decoded;
        next();

    } catch (error) {
        if (error.name === "TokenExpiredError") {
            console.log("Your access token expired");
            //console.log("initial",req.originalUrl);
            initialUrl = req.originalUrl;
            return res.redirect(`/refresh?redirectFrom=${encodeURIComponent(initialUrl)}`);
        }
        console.log("Invalid access token");
        return res.status(401).json({ message: "Invalid or expired access token" });
    }
}

module.exports = { authenticate };
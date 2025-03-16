const refreshTokens = [];

module.exports = {
    addToken: (token) => refreshTokens.push(token),
    removeToken: (token) => {
      const index = refreshTokens.indexOf(token);
      if (index !== -1) refreshTokens.splice(index, 1);
    },
    hasToken: (token) => refreshTokens.includes(token),
  };
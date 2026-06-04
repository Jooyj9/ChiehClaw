module.exports = {
  apps: [
    {
      name: "xxxclaw-feishu",
      cwd: __dirname,
      script: "uv",
      args: "run python -m app.adapters.feishu.long_connection",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PYTHONUTF8: "1",
      },
    },
  ],
};

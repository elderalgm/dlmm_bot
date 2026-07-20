module.exports = {
  apps: [
    {
      name: "dlmm-bot",
      script: "./dlmm_bot.py",
      interpreter: "python3",
      max_memory_restart: "200M",
      autorestart: true,
      watch: false
    }
  ]
};

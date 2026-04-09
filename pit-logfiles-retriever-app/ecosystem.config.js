module.exports = {
  apps: [
    {
      name: "pit-logfiles-retriever",
      script: "app.py",
      interpreter: ".venv/bin/python3",
      cwd: '/home/cis/Robot-Telemetry/pit-logfiles-retriever-app',
      watch: false,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};

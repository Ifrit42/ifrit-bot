require('dotenv').config();
const express               = require('express');
const { verifyKeyMiddleware } = require('discord-interactions');
const { Client, GatewayIntentBits } = require('discord.js');

const app = express();
app.use(express.json());

// 1) Interactions endpoint for slash commands
app.post(
  '/api/interactions',
  verifyKeyMiddleware(process.env.DISCORD_PUBLIC_KEY),
  (req, res) => {
    const payload = req.body;
    if (payload.type === 1) return res.json({ type: 1 });
    return res.json({ type: 4, data: { content: 'Hello from ngrok!' } });
  }
);

// 2) (Optional) OAuth callback or other GET routes
app.get('/verify-user', (req, res) => res.send('OK'));

// 3) Discord Gateway client
const client = new Client({ intents: [GatewayIntentBits.Guilds] });
client.once('ready', () => console.log(`✅ Logged in as ${client.user.tag}`));
client.login(process.env.DISCORD_TOKEN)
  .catch(console.error);

// 4) Start HTTP server
const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 HTTP server listening on http://localhost:${PORT}`);
});
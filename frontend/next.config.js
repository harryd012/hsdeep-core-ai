const path = require('path')

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output for the Docker image (frontend/Dockerfile copies
  // .next/standalone). Merged in from the former next.config.ts, which
  // Next.js never loaded because next.config.js takes precedence.
  output: 'standalone',
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      '@': path.resolve(__dirname, 'src'),
    }
    return config
  },
}

module.exports = nextConfig
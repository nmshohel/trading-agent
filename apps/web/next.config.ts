import type { NextConfig } from "next";

const apiOrigin = (
  process.env.API_INTERNAL_ORIGIN ?? "http://127.0.0.1:5000"
).trim();

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/health",
        destination: `${apiOrigin}/health`,
      },
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

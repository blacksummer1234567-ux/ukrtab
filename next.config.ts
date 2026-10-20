import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    unoptimized: true,
    remotePatterns: [
      {
        protocol: "https",
        hostname: "images.prom.ua",
      },
      {
        protocol: "https",
        hostname: "**.prom.ua",
      },
      {
        protocol: "https",
        hostname: "**.r2.dev",
      },
      {
        protocol: "https",
        hostname: "**.ukrtab.com.ua",
      },
    ],
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output is for the self-hosted Docker image (Dockerfile copies
  // .next/standalone). Vercel packages the app itself through its build adapter, which
  // fails with standalone on Next 16.3 (ENOENT .next/next-server.js.nft.json in
  // onBuildComplete), so it's off there.
  output: process.env.VERCEL ? undefined : "standalone",
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "avatars.githubusercontent.com",
      },
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
      },
    ],
  },
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produces a minimal self-contained server bundle, which keeps the runtime
  // image small on Vercel and in Docker.
  output: "standalone",
  poweredByHeader: false,
};

export default nextConfig;

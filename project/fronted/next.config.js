const withBundleAnalyzer = require('@next/bundle-analyzer')({
  enabled: process.env.ANALYZE === 'true',
});

const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  transpilePackages: ['pi-view-port', 'veaury'],
  output: 'standalone',
  reactStrictMode: false,
  images: {
    formats: ['image/avif', 'image/webp'],
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
    imageSizes: [16, 32, 48, 64, 96, 128],
    minimumCacheTTL: 60 * 60 * 24,
  },
  compress: true,
  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  compiler: {
    removeConsole: process.env.NODE_ENV === 'production',
  },
  experimental: {
    optimizeCss: true,
    scrollRestoration: true,
    // wjgl1 文件上传经 Route Handler 代理；Next 15 默认约 1MB，大 PDF 会在到达 Flask 前 500
    proxyClientMaxBodySize: '100mb',
    serverActions: {
      bodySizeLimit: '100mb',
    },
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000/api/:path*',
      },
    ];
  },
  webpack: (config, { webpack }) => {
    config.module.rules.push({
      test: /\.md$/,
      type: 'asset/source',
    });
    config.resolve.alias = {
      ...config.resolve.alias,
      vue: path.resolve(__dirname, 'node_modules/vue/dist/vue.esm-bundler.js'),
    };
    config.plugins.push(
      new webpack.DefinePlugin({
        __VUE_OPTIONS_API__: JSON.stringify(true),
        __VUE_PROD_DEVTOOLS__: JSON.stringify(false),
      }),
    );
    return config;
  },
};

module.exports = withBundleAnalyzer(nextConfig);

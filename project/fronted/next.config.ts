import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ['pi-view-port', 'veaury'],
  reactStrictMode: false,
  images: {
    unoptimized: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  experimental: {
    ppr: false,
  },
  output: 'standalone',
  webpack: (config, { webpack: wp }) => {
    config.module.rules.push({
      test: /\.md$/,
      type: 'asset/source',
    });
    config.resolve.fallback = {
      ...config.resolve.fallback,
      canvas: false,
      encoding: false,
      '@': require('path').resolve(__dirname, '.'),
      '@components': require('path').resolve(__dirname, 'components'),
      '@utils': require('path').resolve(__dirname, 'utils'),
      '@lib': require('path').resolve(__dirname, 'lib'),
    };
    config.resolve.alias = {
      ...config.resolve.alias,
      vue: 'vue/dist/vue.esm-bundler.js',
    };
    config.plugins.push(
      new wp.DefinePlugin({
        __VUE_OPTIONS_API__: JSON.stringify(true),
        __VUE_PROD_DEVTOOLS__: JSON.stringify(false),
      }),
    );
    return config;
  },
};

export default nextConfig;

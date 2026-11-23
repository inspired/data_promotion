const path = require('path');
const webpack = require('webpack');
const base = require('../webpack.config.js')({}, { mode: 'development' });

module.exports = {
    ...base,
    mode: 'development',
    devtool: false,
    entry: { harness: path.join(__dirname, 'harness.jsx') },
    output: { path: process.env.HARNESS_OUT, filename: '[name].js', clean: false },
    plugins: [
        ...base.plugins,
        new webpack.NormalModuleReplacementPlugin(/[\\/]lib[\\/]api$/, path.join(__dirname, 'mockApi.js')),
        new webpack.NormalModuleReplacementPlugin(/SplunkwebConnector$/, path.join(__dirname, 'Passthrough.jsx')),
    ],
};

module.exports = {
    presets: [
        ['@babel/preset-env', { targets: 'defaults and not dead' }],
        ['@babel/preset-react', { runtime: 'automatic' }],
    ],
};

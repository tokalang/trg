import React, { useState } from 'react';

interface MatrixProps {
    title: string;
    count?: number;
}

export const MatrixView: React.FC<MatrixProps> = ({ title, count = 0 }) => {
    const [current, setCurrent] = useState<number>(count);

    // Click handler with template literal
    const handleClick = () => {
        setCurrent(prev => prev + 1);
        console.log(`Counter updated to ${current + 1}`);
    };

    return (
        <div className="matrix-container" data-testid="matrix-view">
            <h1>{title}</h1>
            <button onClick={handleClick}>Increment: {current}</button>
        </div>
    );
};

'use client';

import React from 'react';

interface CircularGaugeProps {
  value: number;
  max?: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  sublabel?: string;
  color?: string;
  unit?: string;
}

export function CircularGauge({
  value,
  max = 100,
  size = 80,
  strokeWidth = 7,
  label,
  sublabel,
  color = 'stroke-primary',
  unit = '%'
}: CircularGaugeProps) {
  const percentage = Math.min(100, Math.max(0, (value / max) * 100));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percentage / 100) * circumference;

  return (
    <div className='flex flex-col items-center justify-center text-center min-w-0 w-full px-0.5'>
      <div
        className='relative flex items-center justify-center shrink-0'
        style={{ width: size, height: size }}
      >
        <svg width={size} height={size} className='-rotate-90 transform'>
          {/* Background circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            className='stroke-muted/60'
            strokeWidth={strokeWidth}
            fill='transparent'
          />
          {/* Progress circle */}
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            className={`${color} transition-all duration-700 ease-out`}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap='round'
            fill='transparent'
          />
        </svg>
        <div className='absolute flex flex-col items-center justify-center leading-none'>
          <span className='font-bold text-sm tracking-tight font-mono'>
            {Math.round(percentage)}
            <span className='text-[10px] font-normal text-muted-foreground'>{unit}</span>
          </span>
        </div>
      </div>
      {label && (
        <span
          className='text-xs font-semibold mt-1 text-foreground truncate max-w-full'
          title={label}
        >
          {label}
        </span>
      )}
      {sublabel && (
        <span
          className='text-[10px] sm:text-[11px] text-muted-foreground font-mono truncate max-w-full mt-0.5'
          title={sublabel}
        >
          {sublabel}
        </span>
      )}
    </div>
  );
}
